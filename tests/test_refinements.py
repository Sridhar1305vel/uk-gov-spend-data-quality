import pandas as pd

from spendqa.checks import (check_exact_duplicates, check_probable_duplicates,
                            cluster_suppliers)
from spendqa.ingest import load_alias_map, norm_header


def _frame(rows):
    df = pd.DataFrame(rows, columns=["supplier", "date", "amount", "ref"])
    return pd.DataFrame({
        "row_id": range(1, len(df) + 1), "department": "d",
        "txn_date": pd.to_datetime(df["date"]), "supplier": df["supplier"],
        "supplier_cluster": df["supplier"], "amount": df["amount"], "transaction_ref": df["ref"]})


def test_placeholder_ref_is_not_an_exact_duplicate():
    df = _frame([("PwC", "2026-01-14", 100.0, "#"), ("PwC", "2026-01-14", 100.0, "#"),
                 ("CGI", "2026-01-15", 50.0, "R1"), ("CGI", "2026-01-15", 50.0, "R1")])
    assert check_exact_duplicates(df)["row_id"].tolist() == [4]
    assert 2 in check_probable_duplicates(df)["row_id"].tolist()


def test_different_police_forces_do_not_merge_but_variants_do():
    names = [f"THE POLICE AND CRIME COMMISSIONER FOR {f}" for f in
             ("KENT", "GWENT", "CUMBRIA", "NORTHUMBRIA", "ESSEX", "SUSSEX", "NORTH WALES", "SOUTH WALES")]
    names += ["POLICE AND CRIME COMMISSIONER FOR KENT LTD"]
    g = cluster_suppliers(pd.DataFrame({"supplier": names})).groupby("supplier_cluster").size()
    assert len(g) == 8 and g.max() == 2


def test_west_yorkshire_the_for_variants_merge():
    g = cluster_suppliers(pd.DataFrame({"supplier": [
        "THE POLICE AND CRIME COMMISSIONER FOR WEST YORKSHIRE",
        "POLICE AND CRIME COMMISSIONER WEST YORKSHIRE"]}))
    assert g["supplier_cluster"].nunique() == 1


def test_new_aliases():
    am = load_alias_map("config/column_aliases.json")
    assert am[norm_header("Ref")] == "transaction_ref"
    assert am[norm_header("Departmental Family")] == "entity"
    assert am[norm_header("£")] == "amount"


def test_alias_priority_beats_column_position(tmp_path):
    from spendqa.ingest import ingest_file
    f = tmp_path / "x_2026-03.csv"
    f.write_text("Ref,Date,Supplier,Transaction number,Amount\nA,01/03/2026,Acme,TX1,30000\n", encoding="utf-8")
    raw, rep = ingest_file(f, "d", load_alias_map("config/column_aliases.json"))
    assert rep.mapped["Transaction number"] == "transaction_ref"
    assert rep.unmapped == ["Ref"] and raw["transaction_ref"].iloc[0] == "TX1"


def _frame_d(rows):
    df = pd.DataFrame(rows, columns=["supplier", "date", "amount", "ref", "description"])
    return pd.DataFrame({
        "row_id": range(1, len(df) + 1), "department": "d",
        "txn_date": pd.to_datetime(df["date"]), "supplier": df["supplier"],
        "supplier_cluster": df["supplier"], "amount": df["amount"],
        "transaction_ref": df["ref"], "description": df["description"]})


def test_same_reference_different_description_is_low_not_exact():
    from spendqa.checks import check_split_lines
    df = _frame_d([
        ("SE Trains", "2025-09-03", 18261538.25, "2000015149", "HS1 Track Access Advance Payment P8"),
        ("SE Trains", "2025-09-03", 18261538.25, "2000015149", "HS1 Track Access Advance Payment P9"),
        ("Cloudscaler", "2025-10-14", 60728.13, "5100041245", "Project - Contract Mandays"),
        ("Cloudscaler", "2025-10-14", 60728.13, "5100041245", "project  - contract mandays")])
    assert check_exact_duplicates(df)["row_id"].tolist() == [4]       # same description (case/space-insensitive)
    assert check_split_lines(df)["row_id"].tolist() == [2]
    assert 2 not in check_probable_duplicates(df)["row_id"].tolist()  # not double-reported as medium
