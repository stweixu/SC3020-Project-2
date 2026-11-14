SELECT p_partkey, p_name, p_size, p_retailprice
FROM part
WHERE p_size BETWEEN 12 AND 18
  AND p_container IN ('SM BOX', 'MED BOX')
  AND p_name LIKE '%green%';
