"""Check a small non-TPC-H schema inside a transaction that is always rolled back."""

import json
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.preprocessing import DB_CONFIG
from src.comparison import calculate_difference_rate, compare_plans
from src.interface import adapt_comparison_result, index_plan
from src.preprocessing import get_database_context, parse_postgres_plan


def main():
    name = "member4_check_" + uuid4().hex[:10]
    with psycopg.connect(**DB_CONFIG, connect_timeout=10, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '15s'")
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(name)))
            conn.execute('CREATE TABLE "Products" (id integer PRIMARY KEY, price numeric, label text)')
            conn.execute('CREATE TABLE sales (id integer PRIMARY KEY, product_id integer REFERENCES "Products", quantity integer)')
            conn.execute("CREATE INDEX sales_product_idx ON sales(product_id)")
            conn.execute('INSERT INTO "Products" VALUES (1, 12.5, \'Pen\'), (2, 40, \'Book\')')
            conn.execute("INSERT INTO sales VALUES (1, 1, 3), (2, 2, 2)")
            context = json.loads(get_database_context(conn))
            assert {row["relation"] for row in context["columns"] if row["schema"] == name} == {"Products", "sales"}
            assert any(row["name"] == "sales_product_idx" and row["schema"] == name for row in context["indexes"])
            queries = [
                'SELECT * FROM "Products" WHERE price > 10',
                'SELECT p.label, sum(s.quantity) FROM "Products" p JOIN sales s ON p.id = s.product_id GROUP BY p.label',
                'SELECT * FROM sales ORDER BY quantity DESC LIMIT 1',
            ]
            for query in queries:
                raw = conn.execute("EXPLAIN (FORMAT JSON) " + query).fetchone()[0]
                plan = parse_postgres_plan(raw)
                result = compare_plans(plan, plan)
                assert calculate_difference_rate(result) == 0
                view = adapt_comparison_result(result, index_plan(plan), index_plan(plan))
                assert all(value == "unchanged" for value in view["node_statuses"]["postgres"].values())
                print(f"PASS | {len(index_plan(plan))} nodes | {query}")
            raise psycopg.Rollback()
        assert conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone() is None
    print("Context, parsing, comparison and presentation passed on the alternate schema; transaction rolled back.")
    print("These are self-comparison checks, not live AI or semantic-equivalence validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
