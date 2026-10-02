"""Builds a small, deliberately messy TEST FIXTURE (synthetic - not real government data).

Used by the unit tests and as a quick smoke-test:  python tests/fixtures.py data/sample
Never quote numbers from this fixture as findings.
"""
import csv
import sys
from pathlib import Path


def build(dest):
    dest = Path(dest)
    a = dest / "dept_alpha"
    b = dest / "dept_beta"
    a.mkdir(parents=True, exist_ok=True)
    b.mkdir(parents=True, exist_ok=True)

    # --- Dept Alpha: cp1252, preamble lines, UK dates, £ amounts, footer total, ragged row
    hdr = ["Department", "Entity", "Date", "Expense Type", "Expense Area", "Supplier", "Transaction Number", "Amount (£)"]
    rows = [
        ["Alpha", "Alpha Core", "03/03/2025", "Consultancy", "Digital", "Acme Consulting Ltd", "TX001", "£50,000.00"],
        ["Alpha", "Alpha Core", "03/03/2025", "Consultancy", "Digital", "Acme Consulting Ltd", "TX001", "£50,000.00"],   # exact duplicate
        ["Alpha", "Alpha Core", "05/03/2025", "Consultancy", "Digital", "ACME CONSULTING LIMITED", "TX002", "£30,000.00"],
        ["Alpha", "Alpha Core", "15/03/2025", "Consultancy", "Digital", "Acme Consulting Ltd.", "TX003", "£30,000.00"],  # probable duplicate (10 days later)
        ["Alpha", "Alpha Core", "10/03/2025", "Supplies", "Estates", "Beta Supplies PLC", "TX004", "(27,500.00)"],       # credit
        ["Alpha", "Alpha Core", "11/03/2025", "Supplies", "Estates", "", "TX005", "£40,000.00"],                          # missing supplier
        ["Alpha", "Alpha Core", "12/07/2025", "Rent", "Estates", "Gamma Ltd", "TX006", "£26,000.00"],                    # date >62 days from file month
        ["Total", "", "", "", "", "", "", "£247,500.00"],                                                                  # footer -> rejected
        ["Alpha", "Alpha Core", "14/03/2025", "Rent", "Estates", "Delta Services", "TX007", "n/a"],                       # bad amount -> rejected
        ["Alpha", "Alpha Core", "16/03/2025", "Rent", "Estates", "Epsilon Ltd", "TX009", "£28,000.00", "EXTRA"],         # ragged
    ]
    with open(a / "alpha_2025-03.csv", "w", encoding="cp1252", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Alpha Department - spend over 25,000"])
        w.writerow(["Published under the Open Government Licence (test fixture)"])
        w.writerow([])
        w.writerow(hdr)
        w.writerows(rows)

    # --- Dept Beta: UTF-8, semicolon-delimited, different headers and formats
    hdr_b = ["Payment Date", "Vendor Name", "Value", "Cost Centre", "Expenditure Category", "Voucher No"]
    rows_b = [
        ["2025-03-04", "Zeta Holdings", "75000", "Ops", "Services", "V1"],
        ["14-Mar-25", "Zeta Holdings Ltd", "75,000.50", "Ops", "Services", "V2"],
        ["2030-01-01", "Theta Ltd", "26000", "Ops", "Services", "V3"],       # future date
        ["20-Mar-25", "Iota Ltd", "1,200.00-", "Ops", "Services", "V4"],      # trailing-minus credit
        ["21-Mar-25", "Kappa Ltd", "100000", "Ops", "Services", "V5"],
    ]
    with open(b / "beta_mar-25.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(hdr_b)
        w.writerows(rows_b)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "data/sample")
    print("fixture written")
