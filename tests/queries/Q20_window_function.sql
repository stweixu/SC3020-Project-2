SELECT o_orderkey, o_custkey, o_orderdate, o_totalprice,
       ROW_NUMBER() OVER (
           PARTITION BY o_custkey
           ORDER BY o_orderdate DESC, o_orderkey DESC
       ) AS customer_order_rank
FROM orders
WHERE o_orderdate >= DATE '1997-01-01'
  AND o_orderdate < DATE '1997-04-01';
