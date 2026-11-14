SELECT n.n_name, s.s_suppkey, s.s_name, ps.ps_partkey, ps.ps_supplycost
FROM region AS r
JOIN nation AS n ON n.n_regionkey = r.r_regionkey
JOIN supplier AS s ON s.s_nationkey = n.n_nationkey
JOIN partsupp AS ps ON ps.ps_suppkey = s.s_suppkey
WHERE r.r_name = 'ASIA'
  AND s.s_acctbal > 6500
  AND ps.ps_availqty BETWEEN 100 AND 500;
