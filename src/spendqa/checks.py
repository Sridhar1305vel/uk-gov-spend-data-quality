"""Rules-based data-quality checks.

Every check returns row-level flags: (row_id, check_name, severity, detail).
Flags are *review candidates*, not proof of error or wrongdoing: a repeated
payment can be legitimate, a supplier can trade under two names, etc.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

PUBLICATION_THRESHOLD = 25_000
EARLIEST_PLAUSIBLE = pd.Timestamp("2009-01-01")  # transparency publishing began 2010
FLAG_COLS = ["row_id", "check_name", "severity", "detail"]
LEGAL_TOKENS = {"LTD", "LIMITED", "PLC", "LLP", "LP", "INC", "LLC", "CIC"}


def _flags(rows: pd.DataFrame, name: str, severity: str, detail) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=FLAG_COLS)
    d = detail(rows) if callable(detail) else detail
    return pd.DataFrame({"row_id": rows["row_id"].to_numpy(), "check_name": name,
                         "severity": severity, "detail": d})


# ---------------------------------------------------------------------------
# completeness / validity
# ---------------------------------------------------------------------------
def check_missing_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = [
        _flags(df[df["supplier"].isna()], "missing_supplier", "high", "supplier is blank"),
        _flags(df[df["txn_date"].isna()], "missing_or_unparseable_date", "high",
               lambda r: "date '" + r["raw_date"].fillna("").astype(str) + "' blank or in unknown format"),
        _flags(df[df["expense_type"].isna()], "missing_expense_type", "medium", "expense type is blank"),
    ]
    return pd.concat(out, ignore_index=True)


def check_amount_rules(df: pd.DataFrame, threshold: float = PUBLICATION_THRESHOLD) -> pd.DataFrame:
    out = [
        _flags(df[df["amount"] == 0], "zero_amount", "medium", "amount is zero"),
        _flags(df[df["amount"] < 0], "credit_or_negative", "info", "negative amount (credit note or refund?)"),
        _flags(df[(df["amount"] > 0) & (df["amount"] < threshold)], "below_publication_threshold", "low",
               lambda r: "amount " + r["amount"].round(2).astype(str) + f" is under the {threshold:,} publication threshold"),
    ]
    return pd.concat(out, ignore_index=True)


def check_date_rules(df: pd.DataFrame, today: pd.Timestamp | None = None, tolerance_days: int = 62) -> pd.DataFrame:
    today = today or pd.Timestamp.today().normalize()
    df = df.assign(file_period=pd.to_datetime(df["file_period"], errors="coerce"),
                   txn_date=pd.to_datetime(df["txn_date"], errors="coerce"))
    d = df[df["txn_date"].notna()]
    implausible = d[(d["txn_date"] < EARLIEST_PLAUSIBLE) | (d["txn_date"] > today)]
    with_period = d[d["file_period"].notna()].copy()
    period_end = with_period["file_period"] + pd.offsets.MonthEnd(0)
    gap = np.maximum((with_period["file_period"] - with_period["txn_date"]).dt.days,
                     (with_period["txn_date"] - period_end).dt.days)
    outside = with_period[gap > tolerance_days]
    return pd.concat([
        _flags(implausible, "date_implausible", "high",
               lambda r: "date " + r["txn_date"].dt.strftime("%Y-%m-%d") + " is before 2009 or in the future"),
        _flags(outside, "date_outside_file_period", "medium",
               lambda r: "date " + r["txn_date"].dt.strftime("%Y-%m-%d") + " is far from the file's month "
                         + r["file_period"].dt.strftime("%Y-%m")),
    ], ignore_index=True)


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------
PLACEHOLDER_REFS = {"", "#", "-", "--", "0", "NA", "N/A", "NAN", "NULL", "NONE", "TBC", "UNKNOWN", "TBA"}


def _is_placeholder_ref(ref) -> bool:
    """True for blank / dummy references that carry no identifying information."""
    if ref is None or pd.isna(ref):
        return True
    r = str(ref).strip().upper()
    return r in PLACEHOLDER_REFS or not re.search(r"[A-Z1-9]", r)  # e.g. "###", "000"


def _norm_desc(x) -> str:
    """Case/whitespace-insensitive description used to tell repeated lines from distinct ones."""
    if x is None or pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x).upper()).strip()


def _dup_key_frame(df: pd.DataFrame) -> pd.DataFrame:
    placeholder = df["transaction_ref"].map(_is_placeholder_ref)
    return df.assign(_ref=df["transaction_ref"].where(~placeholder, "").fillna(""),
                     _real_ref=~placeholder,
                     _desc=df["description"].map(_norm_desc) if "description" in df else "",
                     _sup=df["supplier_cluster"].fillna(df["supplier"]).fillna(""))


_KEY = ["department", "txn_date", "_sup", "amount", "_ref"]


def _dup_masks(d: pd.DataFrame):
    """(exact, split_line) boolean masks over a frame from _dup_key_frame.

    exact      - same date, supplier, amount, genuine reference AND description as an earlier row
    split_line - same date, supplier, amount and genuine reference as an earlier row but a
                 different description (typically several lines of one payment document,
                 e.g. one payment per period); lower-severity, not a likely double payment
    """
    base = d.duplicated(_KEY, keep="first") & d["txn_date"].notna() & d["_real_ref"]
    exact = d.duplicated(_KEY + ["_desc"], keep="first") & d["txn_date"].notna() & d["_real_ref"]
    return exact, base & ~exact


def check_exact_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    d = _dup_key_frame(df[df["amount"] > 0])
    exact, _ = _dup_masks(d)
    return _flags(d[exact], "exact_duplicate", "high",
                  "same department, date, supplier, amount, reference and description as an earlier row")


def check_split_lines(df: pd.DataFrame) -> pd.DataFrame:
    d = _dup_key_frame(df[df["amount"] > 0])
    _, split = _dup_masks(d)
    return _flags(d[split], "same_reference_different_description", "low",
                  "same date, supplier, amount and reference as an earlier row but a different "
                  "description - likely separate lines of one payment, check before treating as a duplicate")


def check_probable_duplicates(df: pd.DataFrame, window_days: int = 14) -> pd.DataFrame:
    """Same supplier + same amount within a short window but a different reference."""
    d = _dup_key_frame(df[(df["amount"] > 0) & df["txn_date"].notna()])
    exact, split = _dup_masks(d)
    d = d[~exact & ~split & (d["_sup"] != "")].sort_values(["department", "_sup", "amount", "txn_date", "row_id"])
    gap = d.groupby(["department", "_sup", "amount"])["txn_date"].diff().dt.days
    hit = d[(gap >= 0) & (gap <= window_days)]
    return _flags(hit, "probable_duplicate_payment", "medium",
                  f"same supplier and amount as an earlier row within {window_days} days, "
                  "different or missing/placeholder reference")


# ---------------------------------------------------------------------------
# supplier master-data consistency
# ---------------------------------------------------------------------------
def normalise_supplier(name) -> str | None:
    if name is None or (isinstance(name, float) and np.isnan(name)) or pd.isna(name):
        return None
    s = str(name).upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    toks = [t for t in s.split() if t not in LEGAL_TOKENS and t != "FOR"]
    if toks and toks[0] == "THE":
        toks = toks[1:]
    return " ".join(toks) or None


def _same_supplier(a: str, b: str, threshold: float) -> bool:
    """Whole-name similarity >= threshold AND every differing stretch of words is
    itself similar. Without the second test a long shared prefix (e.g. 'POLICE AND
    CRIME COMMISSIONER') lets different entities such as KENT / GWENT merge."""
    if SequenceMatcher(None, a, b).ratio() < threshold:
        return False
    ta, tb = a.split(), b.split()
    for op, i1, i2, j1, j2 in SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        sa, sb = " ".join(ta[i1:i2]), " ".join(tb[j1:j2])
        if not sa or not sb or SequenceMatcher(None, sa, sb).ratio() < threshold:
            return False
    return True


def cluster_suppliers(df: pd.DataFrame, threshold: float = 0.92) -> pd.DataFrame:
    """Group spellings of the same supplier. Returns one row per (raw name, cluster).

    Method: normalise (case, punctuation, legal suffix), block on the first four
    characters, then merge names whose difflib similarity >= threshold and whose differing
    words are themselves similar (see _same_supplier).
    Spellings that differ in their first four characters are not merged
    (a documented limitation).
    """
    names = df["supplier"].dropna().astype(str)
    counts = names.value_counts()
    norm = {raw: normalise_supplier(raw) for raw in counts.index}
    norm_counts = pd.Series({n: 0 for n in set(norm.values()) if n})
    for raw, n in norm.items():
        if n:
            norm_counts[n] += int(counts[raw])
    parent = {n: n for n in norm_counts.index}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    blocks: dict[str, list[str]] = {}
    for n in norm_counts.index:
        blocks.setdefault(re.sub(r"^THE ", "", n)[:4], []).append(n)
    for members in blocks.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if _same_supplier(a, b, threshold):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra
    groups: dict[str, list[str]] = {}
    for n in norm_counts.index:
        groups.setdefault(find(n), []).append(n)
    canon_of = {}
    for members in groups.values():
        canon = sorted(members, key=lambda m: (-norm_counts[m], m))[0]
        for m in members:
            canon_of[m] = canon
    rows = [{"supplier": raw, "supplier_cluster": canon_of.get(n), "rows": int(counts[raw])}
            for raw, n in norm.items()]
    return pd.DataFrame(rows, columns=["supplier", "supplier_cluster", "rows"])


def check_supplier_variants(df: pd.DataFrame) -> pd.DataFrame:
    """Flag rows whose spelling differs from the most common spelling in their cluster."""
    d = df[df["supplier"].notna() & df["supplier_cluster"].notna()]
    spell = d.groupby(["supplier_cluster", "supplier"]).size().rename("n").reset_index()
    spell = spell.sort_values(["supplier_cluster", "n", "supplier"], ascending=[True, False, True])
    modal = spell.drop_duplicates("supplier_cluster").set_index("supplier_cluster")["supplier"]
    n_spell = spell.groupby("supplier_cluster")["supplier"].nunique()
    multi = d[d["supplier_cluster"].map(n_spell) > 1]
    off = multi[multi["supplier"] != multi["supplier_cluster"].map(modal)]
    return _flags(off, "supplier_name_variant", "low",
                  lambda r: "spelling differs from most common spelling '" + r["supplier_cluster"].map(modal) + "'")


# ---------------------------------------------------------------------------
# statistical outliers
# ---------------------------------------------------------------------------
def check_outliers(df: pd.DataFrame, z_cut: float = 4.0, min_rows: int = 30) -> pd.DataFrame:
    """Robust z-score (median/MAD) on log amounts per department, high side only."""
    d = df[df["amount"] > 0].copy()
    d["_log"] = np.log(d["amount"])
    parts = []
    for _, g in d.groupby("department"):
        if len(g) < min_rows:
            continue
        med = g["_log"].median()
        mad = (g["_log"] - med).abs().median()
        if mad == 0:
            continue
        z = 0.6745 * (g["_log"] - med) / mad
        parts.append(g[z > z_cut].assign(_z=z[z > z_cut]))
    if not parts:
        return pd.DataFrame(columns=FLAG_COLS)
    hit = pd.concat(parts)
    return _flags(hit, "large_value_outlier", "low",
                  lambda r: "robust z-score " + r["_z"].round(1).astype(str) + " within department")


# ---------------------------------------------------------------------------
def run_all_checks(df: pd.DataFrame):
    """Returns (df_with_cluster, flags, supplier_clusters)."""
    clusters = cluster_suppliers(df)
    mapping = clusters.set_index("supplier")["supplier_cluster"]
    df = df.assign(supplier_cluster=df["supplier"].map(mapping))
    flags = pd.concat([
        check_missing_fields(df), check_amount_rules(df), check_date_rules(df),
        check_exact_duplicates(df), check_split_lines(df), check_probable_duplicates(df),
        check_supplier_variants(df), check_outliers(df),
    ], ignore_index=True)
    flags = flags.astype({"row_id": "int64"})
    return df, flags, clusters
