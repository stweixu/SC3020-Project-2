SELECT c.c_custkey, c.c_name, COUNT(o.o_orderkey) AS matching_orders
FROM customer AS c
LEFT JOIN orders AS o
    ON o.o_custkey = c.c_custkey
   AND o.o_orderdate >= DATE '1998-01-01'
   AND o.o_orderdate < DATE '1998-02-01'
WHERE c.c_custkey BETWEEN 5000 AND 5500
GROUP BY c.c_custkey, c.c_name;
