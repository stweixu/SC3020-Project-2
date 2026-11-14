SELECT p.p_partkey, p.p_name, p.p_retailprice
FROM part AS p
WHERE p.p_size = 17
  AND p.p_retailprice > (
      SELECT AVG(reference_part.p_retailprice)
      FROM part AS reference_part
      WHERE reference_part.p_size BETWEEN 15 AND 20
  );
