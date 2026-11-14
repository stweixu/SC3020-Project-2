WITH customer_totals AS MATERIALIZED (
    SELECT o_custkey, COUNT(*) AS order_count, SUM(o_totalprice) AS total_spend
    FROM orders
    WHERE o_orderdate >= DATE '1996-01-01'
      AND o_orderdate < DATE '1997-01-01'
    GROUP BY o_custkey
)
SELECT c.c_custkey, c.c_name, totals.order_count, totals.total_spend
FROM customer AS c
JOIN customer_totals AS totals ON totals.o_custkey = c.c_custkey
WHERE c.c_mktsegment = 'HOUSEHOLD'
  AND totals.order_count >= 3;
