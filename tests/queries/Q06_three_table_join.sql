SELECT c.c_custkey, o.o_orderkey, l.l_linenumber,
       l.l_extendedprice, l.l_discount
FROM customer AS c
JOIN orders AS o ON o.o_custkey = c.c_custkey
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
WHERE c.c_mktsegment = 'AUTOMOBILE'
  AND o.o_orderdate >= DATE '1995-09-01'
  AND o.o_orderdate < DATE '1995-10-01'
  AND l.l_quantity BETWEEN 5 AND 15;
