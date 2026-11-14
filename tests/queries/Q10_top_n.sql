SELECT p_partkey, p_name, p_retailprice
FROM part
WHERE p_size BETWEEN 20 AND 30
ORDER BY p_retailprice DESC, p_partkey
LIMIT 25;
