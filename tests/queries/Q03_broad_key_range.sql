SELECT o_orderkey, o_custkey, o_orderdate, o_totalprice
FROM orders
WHERE o_orderkey BETWEEN 1 AND 5500000;
