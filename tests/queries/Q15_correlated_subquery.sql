SELECT c.c_custkey, c.c_nationkey, c.c_acctbal
FROM customer AS c
WHERE c.c_custkey BETWEEN 1000 AND 1100
  AND c.c_acctbal > (
      SELECT AVG(peer.c_acctbal)
      FROM customer AS peer
      WHERE peer.c_nationkey = c.c_nationkey
        AND peer.c_mktsegment = c.c_mktsegment
  );
