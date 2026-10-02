# UK Government Spend: Data-Quality & Reconciliation Pipeline

A pipeline that takes the **real, messy "spend over £25,000" files** that UK government
departments publish every month, forces them into one clean schema, proves that no rows were
lost, and flags data-quality problems for review.

**Why this project exists:** every department publishes the same kind of data with different
column names, encodings, delimiters, date formats and amount formats. Cleaning that reliably,
and *proving* the result is complete, is exactly the work of a data-quality / reconciliation analyst.

> **Status of this repo:** the code and its 15 unit tests are complete and pass on a small
> synthetic *test fixture*. **No real-data results exist yet.** You generate those by running the
> pipeline on real files (steps below). Never quote fixture numbers as findings.

---

## What it does

| Stage | What happens | Where |
|---|---|---|
| Ingest | Detects encoding (UTF-8 / cp1252 / latin-1) and delimiter, finds the header row below any preamble, maps differing column names to one schema, keeps the source file and row number for every record | `src/spendqa/ingest.py` |
| Clean | Parses `£1,234.50`, `(1,234.50)`, `1,234.50-`, `CR` amounts; parses 18 date formats (UK day-first) and Excel serial dates; rejects only non-transactions (total lines, unparseable amounts), each with a reason | `ingest.py` |
| Check | Missing fields, zero/negative/below-threshold amounts, implausible dates, dates far from the file's month, exact and probable duplicate payments, supplier-name variants, statistical outliers | `src/spendqa/checks.py` |
| Load | SQLite database (zero setup), indexed | `src/spendqa/pipeline.py` |
| Reconcile | Per file: rows read = rows loaded + rows rejected, and control totals match between parsing and the database | `pipeline.py` → `reconciliation` table |
| Analyse | SQL views using CTEs and window functions (`LAG`, `RANK`, running `SUM`) for month-on-month spend, supplier concentration, flag summaries | `src/spendqa/sql/analysis.sql` |
| Report | Power BI-ready CSVs, three charts, and a findings report where every number is computed from the data | `src/spendqa/report.py` |

## Quick start

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v           # 15 tests
python tests/fixtures.py data/sample              # tiny synthetic fixture
python run_pipeline.py --raw data/sample --out outputs_sample   # smoke test only
```

## Run it on real data (this is the step that makes it a portfolio project)

1. Go to **data.gov.uk** and search "spend over £25,000". Pick **3 to 5 departments**
   (examples that publish this: Cabinet Office, Department for Transport, Cafcass,
   Department for Work and Pensions, Defra). All are under the Open Government Licence.
2. Download **12 months** of CSVs per department. Pick departments whose files *look different*:
   differing formats is the whole point.
3. Put them in `data/raw/<department_name>/` (one sub-folder per department; the folder name becomes the department).
   Some departments publish `.xlsx`; those work too. Convert `.ods` or HTML to CSV first.
4. Run:
   ```bash
   python run_pipeline.py --raw data/raw --out outputs
   ```
5. Open `outputs/quality_report.md`. Then:
   - Check the **"Files processed … failed"** line and the **"Columns not mapped"** section. Real
     files will use header names I could not predict. Add them to `config/column_aliases.json`
     and re-run until every file loads. *This iteration is expected and is part of the story.*
   - Check `outputs/powerbi/reconciliation.csv`: every file should show `reconciled = True`.
   - Inspect `outputs/powerbi/review_queue.csv`. Open at least 20 flagged rows in the original
     files and **verify them by hand**. Note how many were genuine data issues versus
     legitimate payments. That honest false-positive rate is worth more than a big flag count.
6. Build a Power BI report from `outputs/powerbi/*.csv` (flag summary, department quality
   comparison, monthly spend, supplier concentration, review queue). Publish a screenshot.

## Rules for putting this on your resume

- **Only use numbers from your own real-data run.** The resume bullets in
  `docs/CASE_STUDY_TEMPLATE.md` have blanks on purpose.
- Say **"review candidates"**, not "fraud" or "errors", unless you verified them.
- Don't claim cloud, dbt or PostgreSQL experience from this repo. It uses SQLite. If you later
  port it (see below), then add those words.
- Keep the fixture clearly labelled as a test fixture.

## Design decisions and known limitations (write these in your case study)

- **Day-first dates** are assumed for ambiguous `dd/mm` values (UK data). A file using US order would be mis-parsed.
- **Supplier clustering** normalises case, punctuation and legal suffixes, then merges names with
  difflib similarity ≥ 0.92 within blocks sharing the first four characters. Spellings that
  differ in their first four characters are not merged. Two different companies with very similar
  names could be merged. Tune the threshold on real data and report what you found.
- **Probable duplicates** use a 14-day window. Recurring legitimate payments (rent, framework
  contracts) will trigger some. That is why they are labelled "review candidates".
- **Outlier check** is a robust z-score on log amounts per department and is only a screening aid.
- **Filename-derived month** is used for the "date outside file period" check, and only when the filename contains a recognisable month.
- Files that fail header detection are reported in the `files` table, not skipped silently.

## Optional extensions (do one, then you may legitimately list it)

1. **PostgreSQL port:** load `spend` into PostgreSQL and run `analysis.sql` there (the SQL uses standard CTEs and window functions; minor syntax changes only). Lets you honestly say "PostgreSQL".
2. **dbt-style tests:** express the checks as SQL tests (unique, not null, accepted range) and run them with dbt Core against DuckDB or PostgreSQL.
3. **Cloud warehouse:** load the CSV outputs to BigQuery (free sandbox) and rebuild the views there.
4. **Streamlit dashboard** of the review queue with a "confirmed / false positive" label column, then report precision per check.

## Project layout

```
config/column_aliases.json   header-name mapping (extend this for new departments)
src/spendqa/ingest.py        reading, parsing, cleaning
src/spendqa/checks.py        data-quality checks
src/spendqa/pipeline.py      orchestration, loading, reconciliation
src/spendqa/report.py        CSV exports, charts, markdown report
src/spendqa/sql/analysis.sql SQL views
tests/                       unit and end-to-end tests (synthetic fixture)
run_pipeline.py              command-line entry point
docs/CASE_STUDY_TEMPLATE.md  write-up and resume-bullet template
```

## Data licence

Spend data is published by UK government departments under the Open Government Licence v3.0.
Cite the dataset pages you used in your case study.
