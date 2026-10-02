import pandas as pd

df = pd.read_csv(r"outputs\powerbi\review_queue.csv")

# up to 7 rows per check type, then trim to 20
parts = [g.sample(min(len(g), 7), random_state=42)
         for _, g in df.groupby("check_name")]
sample = pd.concat(parts).sample(frac=1, random_state=42).head(20)

cols = ["row_id", "department", "source_file", "source_row", "supplier",
        "amount", "txn_date", "transaction_ref", "check_name", "detail"]
sample = sample[[c for c in cols if c in sample.columns]].copy()
sample["raw_matches_parsed"] = ""   # Y / N
sample["verdict"] = ""              # Real issue / False alarm / Unsure
sample["notes"] = ""

with pd.ExcelWriter(r"outputs\hand_check_sample.xlsx", engine="openpyxl") as xw:
    sample.to_excel(xw, sheet_name="Sample", index=False)
    pd.DataFrame({
        "measure": ["Rows checked", "Raw matches parsed (Y)", "Real issue",
                    "False alarm", "Unsure"],
        "count": [
            "=COUNTA(Sample!A2:A21)",
            '=COUNTIF(Sample!K2:K21,"Y")',
            '=COUNTIF(Sample!L2:L21,"Real issue")',
            '=COUNTIF(Sample!L2:L21,"False alarm")',
            '=COUNTIF(Sample!L2:L21,"Unsure")',
        ],
    }).to_excel(xw, sheet_name="Summary", index=False)
print("Saved outputs\\hand_check_sample.xlsx")