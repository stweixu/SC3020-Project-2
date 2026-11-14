SELECT o_orderkey, o_custkey, o_orderdate, o_totalprice
FROM orders
WHERE o_orderkey BETWEEN 100000 AND 100300;
