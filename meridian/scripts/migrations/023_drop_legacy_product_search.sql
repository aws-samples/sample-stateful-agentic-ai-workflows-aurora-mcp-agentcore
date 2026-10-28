-- 023: drop semantic_product_search, a leftover of the shopping stack this repository removed
-- (commit e73e0e52). Its body reads the products table, which no longer exists, so every call
-- fails; it was still executable by PUBLIC, so the least-privilege logins could reach it.
-- Nothing in the repository defines or calls it.
DROP FUNCTION IF EXISTS semantic_product_search(vector, integer);
