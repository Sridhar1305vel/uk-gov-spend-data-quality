#!/usr/bin/env python
"""CLI: python run_pipeline.py --raw data/raw --out outputs"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from spendqa.pipeline import run_pipeline  # noqa: E402
from spendqa.report import build_report  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="UK government spend data-quality pipeline")
    ap.add_argument("--raw", default="data/raw", help="folder with one sub-folder per department")
    ap.add_argument("--out", default="outputs", help="output folder (database, CSVs, charts, report)")
    ap.add_argument("--aliases", default="config/column_aliases.json")
    a = ap.parse_args()
    out = Path(a.out)
    print(f"Reading files from {a.raw} ...")
    recon = run_pipeline(a.raw, out / "spend.db", a.aliases)
    ok = bool(recon["reconciled"].all())
    print(f"Reconciliation: {'all files reconciled' if ok else 'PROBLEMS - see outputs/powerbi/reconciliation.csv'}")
    print(f"Report written to {build_report(out / 'spend.db', out)}")


if __name__ == "__main__":
    main()
