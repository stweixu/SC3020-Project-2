SELECT ps_suppkey, COUNT(*) AS stocked_parts, SUM(ps_availqty) AS total_stock
FROM partsupp
WHERE ps_supplycost BETWEEN 100 AND 350
GROUP BY ps_suppkey
HAVING COUNT(*) >= 15
   AND SUM(ps_availqty) > 60000;
