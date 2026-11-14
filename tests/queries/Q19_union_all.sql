SELECT s_suppkey AS account_id, s_name AS account_name,
       s_acctbal AS account_balance, 'supplier' AS account_type
FROM supplier
WHERE s_acctbal > 9000
UNION ALL
SELECT c_custkey AS account_id, c_name AS account_name,
       c_acctbal AS account_balance, 'customer' AS account_type
FROM customer
WHERE c_acctbal > 9000;
