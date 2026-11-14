SELECT s.s_suppkey, s.s_name
FROM supplier AS s
WHERE s.s_acctbal > 2500
  AND NOT EXISTS (
      SELECT 1
      FROM partsupp AS ps
      WHERE ps.ps_suppkey = s.s_suppkey
        AND ps.ps_availqty < 20
  );
