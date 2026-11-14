SELECT c.c_custkey, c.c_name
FROM customer AS c
WHERE c.c_acctbal > 5000
  AND EXISTS (
      SELECT 1
      FROM orders AS o
      WHERE o.o_custkey = c.c_custkey
        AND o.o_orderdate >= DATE '1997-02-01'
        AND o.o_orderdate < DATE '1997-03-01'
        AND o.o_totalprice > 150000
  );
