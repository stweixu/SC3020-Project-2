"""Validate the evaluation workload using estimated PostgreSQL plans."""

import json
from pathlib import Path
import sys

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.preprocessing import DB_CONFIG, parse_postgres_plan


QUERY_DIR = Path(__file__).resolve().parent / "queries"


def load_workload():
    """Read the manifest and its SQL files in their declared order."""
    manifest = json.loads((QUERY_DIR / "manifest.json").read_text(encoding="utf-8"))
    queries = []
    seen = set()
    for entry in manifest["queries"]:
        query_id = entry["id"]
        if query_id in seen:
            raise ValueError(f"Duplicate query ID: {query_id}")
        seen.add(query_id)
        path = (QUERY_DIR / entry["file"]).resolve()
        if path.parent != QUERY_DIR.resolve() or path.suffix != ".sql":
            raise ValueError(f"Invalid SQL file for {query_id}")
        sql = path.read_text(encoding="utf-8").strip()
        if not sql:
            raise ValueError(f"Empty SQL file: {path.name}")
        queries.append({**entry, "sql": sql})
    if not queries:
        raise ValueError("The workload contains no queries.")
    return queries


def main():
    try:
        queries = load_workload()
        conn = psycopg.connect(**DB_CONFIG, connect_timeout=10)
    except (OSError, ValueError, KeyError, psycopg.Error) as exc:
        print(f"Could not prepare workload validation: {exc}", file=sys.stderr)
        return 1

    failures = 0
    with conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_setting('server_version')")
            database, version = cur.fetchone()
        print(f"Database: {database} | PostgreSQL: {version}")
        print("Validating with EXPLAIN (FORMAT JSON); queries are not executed.\n")
        for query in queries:
            try:
                # A savepoint lets later queries be checked after a planning error.
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL statement_timeout = '15s'")
                        cur.execute("EXPLAIN (FORMAT JSON) " + query["sql"])
                        plan = parse_postgres_plan(cur.fetchone()[0])
                nodes = []
                pending = [plan]
                while pending:
                    node = pending.pop()
                    nodes.append(node.node_type)
                    pending.extend(node.children)
                print(f"PASS {query['id']} | {query['category']} | {len(nodes)} nodes | "
                      + ", ".join(sorted(set(nodes))))
            except (psycopg.Error, ValueError, TypeError) as exc:
                failures += 1
                print(f"FAIL {query['id']} | {exc}")
    print(f"\n{len(queries) - failures}/{len(queries)} queries validated.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
