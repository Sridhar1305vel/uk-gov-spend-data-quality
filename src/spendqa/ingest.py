"""Ingestion: read messy departmental spend files into one canonical schema.

Departments publish the same kind of data with different headers, encodings,
delimiters, date formats and amount formats. This module normalises all of that
while keeping lineage (source file + source row) and counting every row so the
pipeline can reconcile rows-in against rows-out.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd

CANONICAL = ["entity", "txn_date", "expense_type", "expense_area",
             "supplier", "transaction_ref", "amount", "description"]
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
DELIMITERS = (",", ";", "\t", "|")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SUMMARY_LABELS = {"total", "grand total", "subtotal", "sub total", "sub-total"}
DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d.%m.%Y", "%Y/%m/%d",
    "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%b-%y", "%b %Y", "%B %Y",
]  # UK data: day-first is assumed for ambiguous dd/mm formats


# ----------------------------------------------------------------------------
# headers / aliases
# ----------------------------------------------------------------------------
def norm_header(h: str) -> str:
    raw = str(h).strip().lower()
    out = re.sub(r"[^a-z0-9]", "", raw)
    # symbol-only headers (e.g. DfT's "£" amount column) would otherwise normalise to ""
    return out or raw


class AliasMap(dict):
    """normalised header -> canonical field, plus .rank (position in that field's
    alias list; lower = preferred) to pick between two columns for one field."""
    rank: dict[str, int]


def load_alias_map(path: str | Path) -> dict[str, str]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    out = AliasMap()
    out.rank = {}
    for canon, aliases in cfg.items():
        for i, a in enumerate(list(aliases) + [canon]):
            key = norm_header(a)
            out[key] = canon
            out.rank.setdefault(key, i)
    return out


def find_header(rows: list[list[str]], alias_map: dict[str, str], max_scan: int = 25):
    """Return (row_index, hits) of the most header-like row in the first lines."""
    best = (None, 0)
    for i, row in enumerate(rows[:max_scan]):
        hits = len({alias_map[norm_header(c)] for c in row if norm_header(c) in alias_map})
        if hits > best[1]:
            best = (i, hits)
    return best if best[1] >= 3 else (None, best[1])


# ----------------------------------------------------------------------------
# value parsers
# ----------------------------------------------------------------------------
def parse_amount(s: pd.Series) -> pd.Series:
    """'£1,234.50' -> 1234.5 ; '(1,234.50)' / '1,234.50-' / '1,234.50CR' -> negative."""
    t = s.astype("string").str.strip()
    neg = (t.str.match(r"^\(.*\)$", na=False)
           | t.str.endswith("-", na=False)
           | t.str.contains(r"(?i)cr\s*$", na=False))
    t = (t.str.replace(r"(?i)cr\s*$", "", regex=True)
          .str.replace(r"[£$€,\s()]", "", regex=True)
          .str.rstrip("-"))
    num = pd.to_numeric(t, errors="coerce").astype("float64")
    return num.where(~neg.fillna(False).astype(bool), -num.abs())


def parse_dates(s: pd.Series) -> pd.Series:
    """Try each known format in turn; also handle Excel serial numbers."""
    t = s.astype("string").str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    remaining = (t.notna() & (t != "")).to_numpy().copy()
    for fmt in DATE_FORMATS:
        if not remaining.any():
            break
        idx = s.index[remaining]
        parsed = pd.to_datetime(t[idx], format=fmt, errors="coerce")
        ok = parsed.notna().to_numpy()
        if ok.any():
            out.loc[idx[ok]] = parsed[ok].astype("datetime64[ns]")
            pos = remaining.nonzero()[0]
            remaining[pos[ok]] = False
    if remaining.any():  # Excel serial dates, e.g. 45730
        idx = s.index[remaining]
        serial = pd.to_numeric(t[idx], errors="coerce")
        ok = serial.between(30000, 70000).fillna(False).to_numpy()
        if ok.any():
            conv = pd.to_datetime(serial[idx[ok]], unit="D", origin="1899-12-30")
            out.loc[idx[ok]] = conv.astype("datetime64[ns]")
    return out


def infer_period(name: str):
    """Month a file claims to cover, from its file name (first day of month) or None."""
    s = name.lower()
    m = re.search(r"(20\d{2})[-_ ./]?(0[1-9]|1[0-2])(?!\d)", s)
    if m:
        return pd.Timestamp(int(m[1]), int(m[2]), 1)
    mon = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
    m = re.search(rf"(?<![a-z])({mon})[a-z]*[-_ .]*((?:20)?\d{{2}})(?!\d)", s)
    if m:
        y = int(m[2])
        y = y + 2000 if y < 100 else y
        return pd.Timestamp(y, MONTHS[m[1]], 1)
    m = re.search(rf"(20\d{{2}})[-_ .]*({mon})", s)
    if m:
        return pd.Timestamp(int(m[1]), MONTHS[m[2]], 1)
    return None


# ----------------------------------------------------------------------------
# file reading
# ----------------------------------------------------------------------------
@dataclass
class FileReport:
    source_file: str
    department: str
    period: str | None = None
    encoding: str = ""
    delimiter: str = ""
    header_row: int | None = None
    header_signature: str = ""
    rows_read: int = 0          # data rows seen (blank lines excluded, header excluded)
    blank_rows: int = 0
    ragged_rows: int = 0        # rows whose cell count differed from the header
    mapped: dict = field(default_factory=dict)    # original header -> canonical
    unmapped: list = field(default_factory=list)  # original headers we did not use
    status: str = "ok"          # ok | no_header | unreadable

    def as_dict(self) -> dict:
        d = asdict(self)
        d["mapped"] = json.dumps(d["mapped"])
        d["unmapped"] = json.dumps(d["unmapped"])
        return d


def _read_rows(path: Path):
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, header=None, dtype=str, keep_default_na=False)
        return df.values.tolist(), "xlsx", "n/a"
    raw = path.read_bytes()
    text, enc = None, ""
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text, enc = raw.decode("latin-1", errors="replace"), "latin-1(replace)"
    return text, enc, None


def ingest_file(path: str | Path, department: str, alias_map: dict[str, str]):
    """Read one file. Returns (raw_frame, FileReport). Never raises on bad content."""
    path = Path(path)
    rep = FileReport(source_file=path.name, department=department)
    p = infer_period(path.stem)
    rep.period = p.strftime("%Y-%m") if p is not None else None
    try:
        content, enc, delim = _read_rows(path)
    except Exception as exc:  # noqa: BLE001 - report and continue
        rep.status = f"unreadable: {type(exc).__name__}"
        return pd.DataFrame(columns=CANONICAL), rep
    rep.encoding = enc

    if delim == "n/a":
        rows, rep.delimiter = [list(map(str, r)) for r in content], "n/a"
        hdr_idx, _ = find_header(rows, alias_map)
    else:
        best = (None, 0, ",", [])
        for d in DELIMITERS:
            r = list(csv.reader(io.StringIO(content), delimiter=d))
            idx, hits = find_header(r, alias_map)
            if idx is not None and hits > best[1]:
                best = (idx, hits, d, r)
        hdr_idx, _, rep.delimiter, rows = best
        if hdr_idx is None:
            rows = []
    if hdr_idx is None:
        rep.status = "no_header"
        return pd.DataFrame(columns=CANONICAL), rep

    rep.header_row = hdr_idx + 1
    header = [h.strip() for h in rows[hdr_idx]]
    rep.header_signature = "|".join(norm_header(h) for h in header)
    n = len(header)

    # column mapping: when several columns map to one canonical field, the one whose
    # alias is listed earliest in the config wins (ties: leftmost column)
    rank = getattr(alias_map, "rank", {})
    col_for: dict[str, int] = {}
    for j, h in enumerate(header):
        canon = alias_map.get(norm_header(h))
        if canon:
            r = rank.get(norm_header(h), 0)
            if canon not in col_for or r < rank.get(norm_header(header[col_for[canon]]), 0):
                col_for[canon] = j
    chosen = set(col_for.values())
    for j, h in enumerate(header):
        if j in chosen:
            rep.mapped[h] = alias_map[norm_header(h)]
        elif h:
            rep.unmapped.append(h)

    records = []
    for k, r in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):
        if not any(str(c).strip() for c in r):
            rep.blank_rows += 1
            continue
        rep.rows_read += 1
        ragged = len(r) != n
        rep.ragged_rows += int(ragged)
        r = (list(r) + [""] * n)[:n]
        rec = {c: (r[col_for[c]] if c in col_for else None) for c in CANONICAL}
        rec["source_file"] = path.name
        rec["source_row"] = k
        rec["ragged"] = ragged
        rec["is_summary_row"] = any(str(c).strip().lower() in SUMMARY_LABELS for c in r)
        records.append(rec)
    df = pd.DataFrame(records, columns=CANONICAL + ["source_file", "source_row", "ragged", "is_summary_row"])
    df["department"] = department
    # always a datetime column (NaT when the file name has no month), so concat keeps the dtype
    df["file_period"] = pd.Series(pd.NaT if p is None else p, index=df.index).astype("datetime64[ns]")
    return df, rep


# ----------------------------------------------------------------------------
# cleaning
# ----------------------------------------------------------------------------
def clean_frame(df: pd.DataFrame):
    """Parse amounts/dates and split into (clean_rows, rejected_rows).

    A row is rejected only when it is not a transaction at all: a summary/total
    line, or an amount that cannot be parsed. Everything else is kept and judged
    by the quality checks, so no row disappears silently.
    """
    df = df.copy()
    rej_cols = ["source_file", "source_row", "reason", "raw_amount", "raw_date", "supplier"]
    if df.empty:
        return df.assign(raw_amount=None, raw_date=None), pd.DataFrame(columns=rej_cols)
    df["raw_amount"], df["raw_date"] = df["amount"], df["txn_date"]
    df["amount"] = parse_amount(df["raw_amount"])
    df["txn_date"] = parse_dates(df["raw_date"])
    for c in ["entity", "expense_type", "expense_area", "supplier", "transaction_ref", "description"]:
        s = df[c].astype("string").str.strip()
        df[c] = s.mask(s == "", pd.NA)
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    reason = reason.mask(df["amount"].isna(), "amount_unparseable")
    reason = reason.mask(df["is_summary_row"] & df["supplier"].isna(), "summary_row")
    rejected = df.loc[reason.notna(), ["source_file", "source_row", "raw_amount", "raw_date", "supplier"]].copy()
    rejected.insert(2, "reason", reason[reason.notna()])
    clean = df.loc[reason.isna()].drop(columns=["is_summary_row"]).reset_index(drop=True)
    return clean, rejected.reset_index(drop=True)
