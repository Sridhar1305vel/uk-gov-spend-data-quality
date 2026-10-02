"""Orchestration: ingest -> clean -> check -> load (SQLite) -> reconcile."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from .checks import run_all_checks
from .ingest import clean_frame, ingest_file, load_alias_map

SUPPORTED = {".csv", ".txt", ".xlsx", ".xlsm"}
SQL_DIR = Path(__file__).parent / "sql"


def discover_files(raw_dir: Path):
    """Sub-folder name = department. Loose files in raw_dir get department 'UNASSIGNED'."""
    for p in sorted(raw_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            rel = p.relative_to(raw_dir)
            yield p, (rel.parts[0] if len(rel.parts) > 1 else "UNASSIGNED")


def run_pipeline(raw_dir, db_path, aliases_path, log=print):
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    alias_map = load_alias_map(aliases_path)
    clean_parts, rejected_parts, reports = [], [], []
    for path, dept in discover_files(raw_dir):
        raw, rep = ingest_file(path, dept, alias_map)
        clean, rejected = clean_frame(raw)
        clean_parts.append(clean)
        rejected_parts.append(rejected)
        reports.append((rep, len(clean), len(rejected), float(clean["amount"].sum()) if len(clean) else 0.0))
        log(f"  {dept}/{path.name}: {rep.status}, read={rep.rows_read}, loaded={len(clean)}, rejected={len(rejected)}")
    if not reports:
        raise SystemExit(f"No supported files found under {raw_dir}")

    spend = pd.concat([c for c in clean_parts if len(c)], ignore_index=True) if any(len(c) for c in clean_parts) \
        else pd.DataFrame()
    if spend.empty:
        raise SystemExit("No rows could be loaded - check the 'files' table / aliases for header problems.")
    spend.insert(0, "row_id", range(1, len(spend) + 1))
    spend, flags, clusters = run_all_checks(spend)
    rejected = pd.concat(rejected_parts, ignore_index=True)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    out = spend.copy()
    out["txn_date"] = out["txn_date"].dt.strftime("%Y-%m-%d")
    out["file_period"] = out["file_period"].dt.strftime("%Y-%m")
    out = out.drop(columns=["raw_date"]).assign(raw_date=spend["raw_date"])
    with closing(sqlite3.connect(db_path)) as con:
        out.astype(object).where(out.notna(), None).to_sql("spend", con, index=False)
        flags.to_sql("dq_flags", con, index=False)
        clusters.to_sql("supplier_clusters", con, index=False)
        rejected.astype(object).where(rejected.notna(), None).to_sql("rejected_rows", con, index=False)
        pd.DataFrame([r[0].as_dict() for r in reports]).to_sql("files", con, index=False)
        con.execute("CREATE INDEX idx_spend_row ON spend(row_id)")
        con.execute("CREATE INDEX idx_flags_row ON dq_flags(row_id)")
        recon = reconcile(con, reports)
        recon.to_sql("reconciliation", con, index=False)
        con.executescript((SQL_DIR / "analysis.sql").read_text(encoding="utf-8"))
        con.commit()
    return recon


def reconcile(con: sqlite3.Connection, reports) -> pd.DataFrame:
    """Prove nothing vanished: rows read = rows loaded + rows rejected, and control totals match."""
    rows = []
    for rep, n_clean, n_rej, amount_in in reports:
        loaded, amount_db = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM spend WHERE source_file = ? AND department = ?",
            (rep.source_file, rep.department)).fetchone()
        rejected = con.execute(
            "SELECT COUNT(*) FROM rejected_rows WHERE source_file = ?", (rep.source_file,)).fetchone()[0]
        rows.append({
            "department": rep.department, "source_file": rep.source_file, "status": rep.status,
            "rows_read": rep.rows_read, "rows_loaded": loaded, "rows_rejected": rejected,
            "row_difference": rep.rows_read - loaded - rejected,
            "amount_parsed_gbp": round(amount_in, 2), "amount_in_db_gbp": round(amount_db, 2),
            "amount_difference_gbp": round(amount_in - amount_db, 2),
        })
    recon = pd.DataFrame(rows)
    recon["reconciled"] = (recon["row_difference"] == 0) & (recon["amount_difference_gbp"].abs() <= 0.01) \
        & (recon["status"] == "ok")
    return recon
