# UK Government Spend Over £25,000: Data-Quality Case Study

Data: UK government departments, Open Government Licence v3.0.

## Goal

Government departments publish monthly "spend over £25,000" files, but each department uses its own column names, date formats and file layout. This project loads the files from three departments into one database, proves that nothing was lost in loading, and flags rows that deserve a manual look.

## Scope

| Item | Value |
|---|---|
| Departments | DfT, HMRC, Cabinet Office |
| Files loaded | 36 |
| Rows loaded | 52,703 (DfT 30,846; HMRC 14,869; Cabinet Office 6,988) |
| Period | January 2025 to mid-2026 |

## Method

1. **Load.** Read each raw CSV, standardise columns, dates and amounts, and write the rows to a database. Raw files and the database are not committed to the repository.
2. **Reconcile.** For every file, compare rows read, rows loaded and rows rejected, and compare the amount parsed from the file with the amount in the database.
3. **Flag.** Apply simple rules and mark rows as review candidates: exact duplicates, probable duplicate payments (same supplier and amount within 14 days, different or missing reference) and zero amounts. Supplier name variants are grouped into clusters.
4. **Report.** A three-page Power BI dashboard: data-quality overview, spend and supplier concentration, and a review queue.

## Results

- **Reconciliation:** all 36 files reconciled (36 of 36).
- **Flag rate:** 2.31% of loaded rows (1,216 rows) carry a high or medium flag. By department: HMRC 3.41%, Cabinet Office 2.60%, DfT 1.71%.
- **Spend is concentrated:** a small number of suppliers account for a large share of spend (Network Rail ranks first, then National Highways), and a few very large payments dominate the monthly totals, so the monthly chart uses a log scale.

Flags are review candidates, not confirmed errors.

## Hand check

I checked a 15-row sample against the raw files. The sample is stratified, not random: up to 7 rows per check type, and the queue has three check types. It is a sanity check of the rules, not an estimate of precision.

| Result | Count |
|---|---|
| Rows checked | 15 |
| Traced to the raw file and matching the parsed values | 15 |
| Real issue | 8 |
| False alarm | 2 |
| Unsure | 5 |

What the sample showed:

- **Exact duplicates (7 of 7):** each was confirmed as an identical row appearing twice in the published files. For example, the same Accenture payment (reference 5100059197, £166,382.36, 21/01/2026) appears on two lines of the January 2026 HMRC file. This shows the published file contains the duplicate row. It does not prove the money was paid twice.
- **Zero amount (1 of 1):** confirmed as a zero value in a spend-over-£25,000 file.
- **Probable duplicate payments (7 rows):** none confirmed. Two looked like legitimate repeating payments (dozens of identical lines for the same supplier and amount) and were marked false alarm. Five could not be settled from the file alone and were left as unsure.

## Limitations

- The probable-duplicate rule produces many false alarms where a supplier is paid the same amount regularly. It needs a supplier-level exception list or a check on invoice references.
- The false-alarm and unsure verdicts were judged from the pattern in the data (number of matching lines and date gaps), not from invoices or other external evidence.
- Two probable-duplicate rows (adjacent lines with the same supplier and amount) were not compared field by field and remain unsure.
- Three departments and one file format family only. Other departments would need new column mappings.

## Next steps

- Add a supplier exception list for regular repeat payments and re-measure the false-alarm rate on a larger random sample.
- Add more departments.
