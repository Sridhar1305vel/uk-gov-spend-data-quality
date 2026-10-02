-- Analysis views over the loaded tables (SQLite dialect; standard CTEs and window functions).

DROP VIEW IF EXISTS v_monthly_spend;
CREATE VIEW v_monthly_spend AS
WITH m AS (
    SELECT department, substr(txn_date, 1, 7) AS month,
           COUNT(*) AS txns, ROUND(SUM(amount), 2) AS total_gbp
    FROM spend
    WHERE txn_date IS NOT NULL AND amount > 0
    GROUP BY department, substr(txn_date, 1, 7)
)
SELECT department, month, txns, total_gbp,
       LAG(total_gbp) OVER (PARTITION BY department ORDER BY month) AS prev_month_gbp,
       ROUND(100.0 * (total_gbp - LAG(total_gbp) OVER (PARTITION BY department ORDER BY month))
             / NULLIF(LAG(total_gbp) OVER (PARTITION BY department ORDER BY month), 0), 1) AS mom_pct
FROM m;

DROP VIEW IF EXISTS v_supplier_concentration;
CREATE VIEW v_supplier_concentration AS
WITH s AS (
    SELECT supplier_cluster, COUNT(*) AS txns, SUM(amount) AS total_gbp
    FROM spend
    WHERE amount > 0 AND supplier_cluster IS NOT NULL
    GROUP BY supplier_cluster
), r AS (
    SELECT supplier_cluster, txns, total_gbp,
           RANK() OVER (ORDER BY total_gbp DESC) AS spend_rank,
           SUM(total_gbp) OVER (ORDER BY total_gbp DESC, supplier_cluster
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_gbp,
           SUM(total_gbp) OVER () AS grand_total,
           COUNT(*) OVER () AS n_suppliers
    FROM s
)
SELECT supplier_cluster, txns, ROUND(total_gbp, 2) AS total_gbp, spend_rank,
       ROUND(100.0 * cum_gbp / grand_total, 2) AS cum_share_pct,
       ROUND(100.0 * spend_rank / n_suppliers, 2) AS supplier_rank_pct
FROM r;

DROP VIEW IF EXISTS v_flag_summary;
CREATE VIEW v_flag_summary AS
SELECT f.check_name, f.severity, COUNT(*) AS flagged_rows,
       ROUND(SUM(s.amount), 2) AS amount_gbp
FROM dq_flags f JOIN spend s ON s.row_id = f.row_id
GROUP BY f.check_name, f.severity;

DROP VIEW IF EXISTS v_department_quality;
CREATE VIEW v_department_quality AS
SELECT s.department,
       COUNT(*) AS rows_loaded,
       SUM(CASE WHEN s.row_id IN (SELECT row_id FROM dq_flags WHERE severity IN ('high', 'medium'))
                THEN 1 ELSE 0 END) AS rows_high_or_medium_flag,
       ROUND(100.0 * SUM(CASE WHEN s.row_id IN (SELECT row_id FROM dq_flags WHERE severity IN ('high', 'medium'))
                              THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_rows_high_or_medium_flag
FROM spend s
GROUP BY s.department;

DROP VIEW IF EXISTS v_duplicate_exposure;
CREATE VIEW v_duplicate_exposure AS
SELECT f.check_name, COUNT(*) AS rows_flagged, ROUND(SUM(s.amount), 2) AS amount_gbp
FROM dq_flags f JOIN spend s ON s.row_id = f.row_id
WHERE f.check_name IN ('exact_duplicate', 'probable_duplicate_payment')
GROUP BY f.check_name;
