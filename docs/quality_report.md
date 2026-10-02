# Data-quality findings (generated)

> Flags are **review candidates**, not confirmed errors or wrongdoing. A repeated payment can be legitimate; a supplier can trade under two names.

## Scope

- Files processed: **36** (36 ok, 0 failed) across **3** departments
- Distinct header layouts (schema drift): **5**
- Rows loaded: **52,703**; gross payments **£49,595,580,125.50**; distinct supplier clusters: **2,775**

## Reconciliation

- Every file reconciled (rows read = loaded + rejected, control totals match): **YES**
- Rejected rows (amount_unparseable): 1

## Quality flags

| Check | Severity | Rows | Amount (£) |
|---|---|---|---|
| supplier_name_variant | low | 3,538 | 15,890,069,501.50 |
| below_publication_threshold | low | 2,132 | 31,036,962.92 |
| probable_duplicate_payment | medium | 1,038 | 183,787,726.75 |
| large_value_outlier | low | 830 | 37,050,674,117.06 |
| credit_or_negative | info | 374 | -41,688,893.29 |
| exact_duplicate | high | 177 | 22,609,825.51 |
| same_reference_different_description | low | 19 | 93,834,737.51 |
| zero_amount | medium | 1 | 0.00 |

Rows with at least one high/medium flag: **1,216** (2.31% of loaded rows)

## Possible duplicate exposure (review candidates)

- exact_duplicate: 177 rows, £22,609,825.51
- probable_duplicate_payment: 1038 rows, £183,787,726.75

## Supplier master data

- Supplier clusters with more than one spelling: **343**
  - SOFTCAT: 7 spellings, 110 rows
  - PHOENIX SOFTWARE: 5 spellings, 213 rows
  - BYTES SOFTWARE SERVICES: 5 spellings, 191 rows
  - AECOM: 5 spellings, 116 rows
  - PRICEWATERHOUSE COOPERS: 5 spellings, 99 rows

## Concentration

- **2.92%** of supplier clusters account for 80% of gross spend

## Department comparison (high/medium flag rate)

| Department | Rows | % flagged |
|---|---|---|
| DfT | 30,846 | 1.71 |
| HMRC | 14,869 | 3.41 |
| cabinet_office | 6,988 | 2.60 |

## Columns not mapped (consider extending config/column_aliases.json)

Contract Number, Departmental Family, Entity, Postal Code, Project Code, Ref, Supplier Postcode, Supplier Type
