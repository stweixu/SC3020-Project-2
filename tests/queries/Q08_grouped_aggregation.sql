SELECT l_shipmode, l_returnflag,
       COUNT(*) AS line_count,
       SUM(l_extendedprice * (1 - l_discount)) AS discounted_value,
       AVG(l_quantity) AS average_quantity
FROM lineitem
WHERE l_shipdate >= DATE '1994-02-01'
  AND l_shipdate < DATE '1994-05-01'
GROUP BY l_shipmode, l_returnflag;
