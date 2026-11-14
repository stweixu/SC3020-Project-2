SELECT first_offer.ps_partkey,
       first_offer.ps_suppkey AS supplier_a,
       second_offer.ps_suppkey AS supplier_b,
       first_offer.ps_supplycost AS cost_a,
       second_offer.ps_supplycost AS cost_b
FROM partsupp AS first_offer
JOIN partsupp AS second_offer
    ON second_offer.ps_partkey = first_offer.ps_partkey
   AND first_offer.ps_suppkey < second_offer.ps_suppkey
WHERE first_offer.ps_partkey BETWEEN 200 AND 400
  AND first_offer.ps_supplycost > second_offer.ps_supplycost * 1.2;
