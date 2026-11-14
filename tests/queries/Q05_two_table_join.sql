SELECT c.c_custkey, c.c_name, o.o_orderkey, o.o_orderdate, o.o_totalprice
FROM customer AS c
JOIN orders AS o ON o.o_custkey = c.c_custkey
WHERE c.c_custkey BETWEEN 1200 AND 1250
  AND o.o_orderdate >= DATE '1996-04-01'
  AND o.o_orderdate < DATE '1996-07-01';
