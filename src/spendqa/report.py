"""Turn the SQLite output into Power BI-ready CSVs, charts and a findings report.

Every number in quality_report.md is computed from the database; nothing is hard-coded.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def build_report(db_path, out_dir):
    out_dir = Path(out_dir)
    pbi = out_dir / "powerbi"
    pbi.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        files = _q(con, "SELECT * FROM files")
        recon = _q(con, "SELECT * FROM reconciliation")
        recon["reconciled"] = recon["reconciled"].astype(bool)
        flags = _q(con, "SELECT * FROM v_flag_summary ORDER BY flagged_rows DESC")
        dept = _q(con, "SELECT * FROM v_department_quality ORDER BY department")
        monthly = _q(con, "SELECT * FROM v_monthly_spend ORDER BY department, month")
        conc = _q(con, "SELECT * FROM v_supplier_concentration ORDER BY spend_rank")
        dup = _q(con, "SELECT * FROM v_duplicate_exposure")
        rejected = _q(con, "SELECT reason, COUNT(*) AS rows FROM rejected_rows GROUP BY reason")
        variants = _q(con, """
            SELECT supplier_cluster, COUNT(*) AS spellings, SUM(rows) AS rows
            FROM supplier_clusters WHERE supplier_cluster IS NOT NULL
            GROUP BY supplier_cluster HAVING COUNT(*) > 1 ORDER BY spellings DESC, rows DESC""")
        totals = _q(con, "SELECT COUNT(*) AS n, ROUND(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),2) AS gross, "
                         "COUNT(DISTINCT supplier_cluster) AS suppliers FROM spend").iloc[0]
        flagged_rows = _q(con, "SELECT COUNT(DISTINCT row_id) AS n FROM dq_flags WHERE severity IN ('high','medium')").iloc[0, 0]
        review = _q(con, """
            SELECT s.row_id, s.department, s.source_file, s.source_row, s.txn_date, s.supplier,
                   s.transaction_ref, s.amount, f.check_name, f.severity, f.detail
            FROM dq_flags f JOIN spend s ON s.row_id = f.row_id
            WHERE f.severity IN ('high','medium') ORDER BY ABS(s.amount) DESC""")

    for name, frame in {"flag_summary": flags, "department_quality": dept, "monthly_spend": monthly,
                        "supplier_concentration": conc, "reconciliation": recon,
                        "supplier_variants": variants, "review_queue": review}.items():
        frame.to_csv(pbi / f"{name}.csv", index=False)

    # ---- charts ----
    if len(flags):
        ax = flags.sort_values("flagged_rows").plot.barh(x="check_name", y="flagged_rows", legend=False, figsize=(8, 4.5))
        ax.set_xlabel("Rows flagged"); ax.set_ylabel(""); ax.set_title("Rows flagged by data-quality check")
        plt.tight_layout(); plt.savefig(out_dir / "flags_by_check.png", dpi=140); plt.close()
    if len(monthly):
        piv = monthly.pivot(index="month", columns="department", values="total_gbp")
        ax = piv.plot(figsize=(9, 4.5)); ax.set_ylabel("GBP (payments > 0)"); ax.set_title("Monthly spend by department")
        plt.tight_layout(); plt.savefig(out_dir / "monthly_spend.png", dpi=140); plt.close()
    pct80 = None
    if len(conc):
        at80 = conc[conc["cum_share_pct"] >= 80].iloc[0]
        pct80 = float(at80["supplier_rank_pct"])
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(conc["supplier_rank_pct"], conc["cum_share_pct"])
        ax.axhline(80, ls="--", lw=0.8, color="grey")
        ax.set_xlabel("% of suppliers (largest first)"); ax.set_ylabel("Cumulative % of spend")
        ax.set_title("Supplier concentration"); plt.tight_layout()
        plt.savefig(out_dir / "supplier_concentration.png", dpi=140); plt.close()

    # ---- markdown report (numbers all come from the data above) ----
    n_files, n_ok = len(files), int((files["status"] == "ok").sum())
    layouts = files.loc[files["status"] == "ok", "header_signature"].nunique()
    all_rec = bool(recon["reconciled"].all()) if len(recon) else False
    unmapped = sorted({h for js in files["unmapped"] for h in json.loads(js)})
    L = []
    L.append("# Data-quality findings (generated)\n")
    L.append("> Flags are **review candidates**, not confirmed errors or wrongdoing. "
             "A repeated payment can be legitimate; a supplier can trade under two names.\n")
    L.append("## Scope\n")
    L.append(f"- Files processed: **{n_files}** ({n_ok} ok, {n_files - n_ok} failed) across **{files['department'].nunique()}** departments")
    L.append(f"- Distinct header layouts (schema drift): **{layouts}**")
    L.append(f"- Rows loaded: **{int(totals['n']):,}**; gross payments **£{totals['gross']:,.2f}**; distinct supplier clusters: **{int(totals['suppliers']):,}**\n")
    L.append("## Reconciliation\n")
    L.append(f"- Every file reconciled (rows read = loaded + rejected, control totals match): **{'YES' if all_rec else 'NO - see reconciliation.csv'}**")
    for _, r in rejected.iterrows():
        L.append(f"- Rejected rows ({r['reason']}): {int(r['rows'])}")
    bad = recon[~recon["reconciled"]]
    for _, r in bad.iterrows():
        L.append(f"- NOT reconciled: {r['department']}/{r['source_file']} (status {r['status']}, row difference {r['row_difference']})")
    L.append("\n## Quality flags\n")
    L.append("| Check | Severity | Rows | Amount (£) |\n|---|---|---|---|")
    for _, r in flags.iterrows():
        L.append(f"| {r['check_name']} | {r['severity']} | {int(r['flagged_rows']):,} | {r['amount_gbp']:,.2f} |")
    pct = 100 * flagged_rows / max(int(totals["n"]), 1)
    L.append(f"\nRows with at least one high/medium flag: **{int(flagged_rows):,}** ({pct:.2f}% of loaded rows)\n")
    if len(dup):
        L.append("## Possible duplicate exposure (review candidates)\n")
        for _, r in dup.iterrows():
            L.append(f"- {r['check_name']}: {int(r['rows_flagged'])} rows, £{r['amount_gbp']:,.2f}")
    L.append("\n## Supplier master data\n")
    L.append(f"- Supplier clusters with more than one spelling: **{len(variants):,}**")
    for _, r in variants.head(5).iterrows():
        L.append(f"  - {r['supplier_cluster']}: {int(r['spellings'])} spellings, {int(r['rows'])} rows")
    if pct80 is not None:
        L.append(f"\n## Concentration\n\n- **{pct80:.2f}%** of supplier clusters account for 80% of gross spend")
    L.append("\n## Department comparison (high/medium flag rate)\n")
    L.append("| Department | Rows | % flagged |\n|---|---|---|")
    for _, r in dept.iterrows():
        L.append(f"| {r['department']} | {int(r['rows_loaded']):,} | {r['pct_rows_high_or_medium_flag']:.2f} |")
    if unmapped:
        L.append("\n## Columns not mapped (consider extending config/column_aliases.json)\n")
        L.append(", ".join(unmapped))
    (out_dir / "quality_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return out_dir / "quality_report.md"
