"""Collect replayable plan inputs without running the comparison algorithm."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from uuid import uuid4

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.preprocessing import (
    DB_CONFIG,
    PlanNode,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_RETRY_ATTEMPTS,
    DEFAULT_LLM_TIMEOUT_MS,
    get_ai_plan,
    get_database_context,
    parse_ai_plan,
    parse_postgres_plan,
)
from validate_workload import QUERY_DIR, load_workload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Replace a JSON file only after its complete contents have been written."""
    content = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def plan_to_dict(node: PlanNode) -> dict:
    if not isinstance(node, PlanNode):
        raise TypeError("Expected a PlanNode tree.")
    return {
        "node_type": node.node_type,
        "properties": dict(node.properties),
        "children": [plan_to_dict(child) for child in node.children],
    }


def restore_plan(record: dict) -> PlanNode:
    """Rebuild a saved successful plan without a database or API request."""
    if record["status"] != "success":
        raise ValueError("Only successful plan records can be replayed.")
    if record["source"] == "postgres":
        return parse_postgres_plan(record["raw_plan"])
    if record["source"] == "ai":
        return parse_ai_plan(record["plan"])
    raise ValueError(f"Unknown plan source: {record['source']}")


def error_details(exc: Exception) -> dict:
    message = str(exc)
    secrets = [DB_CONFIG.get("password")]
    secrets.extend(os.getenv(key) for key in ("LLM_API_KEY", "GEMINI_API_KEY", "DB_PASSWORD"))
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return {"type": type(exc).__name__, "message": message}


def collect_record(query: dict, source: str, repetition: int, fetch) -> dict:
    if source not in {"postgres", "ai"}:
        raise ValueError("Source must be postgres or ai.")
    record = {
        "format_version": 1,
        "query_id": query["id"],
        "category": query["category"],
        "sql": query["sql"],
        "sql_sha256": digest(query["sql"]),
        "source": source,
        "repetition": repetition,
        "started_at": utc_now(),
        "status": "retrieval_failed" if source == "postgres" else "generation_failed",
        "raw_plan": None,
        "plan": None,
        "raw_response_available": False,
        "error": None,
        "comparison_status": "not_evaluated",
        "difference_rate": None,
    }
    started = perf_counter()
    try:
        payload = fetch(query["sql"])
        if source == "postgres":
            record["raw_plan"] = payload
            record["raw_response_available"] = True
        record["status"] = "parsing_failed"
        plan = parse_postgres_plan(payload) if source == "postgres" else payload
        record["plan"] = plan_to_dict(plan)
        record["status"] = "success"
    except Exception as exc:
        record["error"] = error_details(exc)
    record["duration_seconds"] = round(perf_counter() - started, 6)
    record["finished_at"] = utc_now()
    return record


def collect_batch(directory: Path, queries: list, sources: list, repetitions: int, fetchers: dict) -> dict:
    records_dir = directory / "records"
    records_dir.mkdir()
    summary = {
        "collection_status": "in_progress",
        "expected_records": len(queries) * len(sources) * repetitions,
        "completed_records": 0,
        "counts": {},
        "records": [],
        "comparison_status": "not_evaluated",
    }
    counts = Counter()
    write_json(directory / "summary.json", summary)
    for query in queries:
        for repetition in range(1, repetitions + 1):
            for source in sources:
                record = collect_record(query, source, repetition, fetchers[source])
                record["run_id"] = directory.name
                filename = f"{query['id']}_{source}_{repetition:02d}.json"
                write_json(records_dir / filename, record)
                counts[f"{source}:{record['status']}"] += 1
                summary["records"].append({
                    "query_id": query["id"], "source": source,
                    "repetition": repetition, "status": record["status"],
                    "file": f"records/{filename}",
                })
                summary["completed_records"] += 1
                summary["counts"] = dict(counts)
                write_json(directory / "summary.json", summary)
                print(f"{query['id']} | {source} | repeat {repetition} | {record['status']}")
    summary["collection_status"] = "complete"
    write_json(directory / "summary.json", summary)
    return summary


def database_metadata(conn) -> dict:
    def rows(sql):
        with conn.cursor() as cur:
            cur.execute(sql)
            names = [column.name for column in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    identity = rows("SELECT current_database() AS database, version() AS server_version, "
                    "current_setting('search_path') AS search_path")[0]
    identity["planner_settings"] = rows("""
        SELECT name, setting, unit FROM pg_settings
        WHERE name LIKE 'enable_%' OR name LIKE '%cost%'
           OR name IN ('work_mem', 'effective_cache_size', 'max_parallel_workers_per_gather',
                       'max_parallel_workers', 'max_worker_processes', 'default_statistics_target',
                       'geqo', 'geqo_threshold', 'geqo_effort', 'geqo_seed', 'geqo_pool_size',
                       'geqo_generations', 'geqo_selection_bias', 'join_collapse_limit',
                       'from_collapse_limit', 'jit', 'plan_cache_mode', 'constraint_exclusion',
                       'parallel_leader_participation')
        ORDER BY name
    """)
    identity["indexes"] = rows("""
        SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = ANY(current_schemas(false))
        ORDER BY schemaname, tablename, indexname
    """)
    identity["table_statistics"] = rows("""
        SELECT schemaname, relname, n_live_tup AS estimated_live_rows,
               last_analyze::text, last_autoanalyze::text
        FROM pg_stat_user_tables
        WHERE schemaname = ANY(current_schemas(false)) ORDER BY schemaname, relname
    """)
    return identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("postgres", "ai", "both"), default="postgres")
    parser.add_argument("--queries", nargs="+", metavar="ID", help="Subset, e.g. Q01 Q02 Q03")
    parser.add_argument("--repeats", type=int, default=1, help="Generations per query and source")
    parser.add_argument("--schema", type=Path, help="Explicit AI context; default: inspect connected database")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/plan_runs")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    sources = ["postgres", "ai"] if args.source == "both" else [args.source]
    if "ai" in sources and not (os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")):
        parser.error("AI collection needs LLM_API_KEY or GEMINI_API_KEY in .env.")

    try:
        queries = load_workload()
        if args.queries:
            unknown = set(args.queries) - {query["id"] for query in queries}
            if unknown:
                parser.error("Unknown query IDs: " + ", ".join(sorted(unknown)))
            queries = [query for query in queries if query["id"] in args.queries]
        schema = args.schema.read_text(encoding="utf-8") if args.schema else None
        if schema is not None and not schema.strip():
            raise ValueError("Schema context must not be empty.")
        preprocessing_source = (PROJECT_ROOT / "src/preprocessing.py").read_text(encoding="utf-8")
        llm = None
        if "ai" in sources:
            llm = {
                "model": os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
                "timeout_ms": int(os.getenv("LLM_TIMEOUT_MS", DEFAULT_LLM_TIMEOUT_MS)),
                "retry_attempts": int(os.getenv("LLM_RETRY_ATTEMPTS", DEFAULT_LLM_RETRY_ATTEMPTS)),
                "response_mime_type": "application/json",
                "temperature": "not explicitly set; provider default",
                "seed": "not explicitly set",
                "capture_format": "parsed PlanNode; existing API wrapper does not expose raw response",
            }
            if llm["timeout_ms"] <= 0 or llm["retry_attempts"] < 1:
                raise ValueError("LLM timeout and retry attempts must be positive.")
        with psycopg.connect(**DB_CONFIG, connect_timeout=10, autocommit=True) as conn:
            conn.read_only = True
            if schema is None:
                schema = get_database_context(conn)
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '15s'")
                database = database_metadata(conn)
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid4().hex[:8]
            directory = args.output.resolve() / run_id
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "schema_context.sql").write_text(schema, encoding="utf-8")
            (directory / "preprocessing_snapshot.py").write_text(preprocessing_source, encoding="utf-8")
            manifest = json.loads((QUERY_DIR / "manifest.json").read_text(encoding="utf-8"))
            write_json(directory / "workload.json", {"manifest": manifest, "selected_queries": queries})
            write_json(directory / "run.json", {
                "format_version": 1, "run_id": run_id, "started_at": utc_now(),
                "sources": sources, "repetitions": args.repeats,
                "query_ids": [query["id"] for query in queries],
                "declared_dataset": manifest["dataset"],
                "database": database, "llm": llm,
                "schema_sha256": digest(schema),
                "preprocessing_sha256": digest(preprocessing_source),
                "python": platform.python_version(), "platform": platform.platform(),
                "packages": {name: version(name) for name in ("psycopg", "google-genai", "python-dotenv")},
                "postgres_statement_timeout_ms": 15000,
            })

            def fetch_postgres(sql):
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL statement_timeout = '15s'")
                        cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                        return cur.fetchone()[0]

            print(f"Saving run to {directory}\n")
            summary = collect_batch(directory, queries, sources, args.repeats, {
                "postgres": fetch_postgres,
                "ai": lambda sql: get_ai_plan(sql, schema),
            })
    except (OSError, ValueError, KeyError, psycopg.Error) as exc:
        print(f"Collection failed: {error_details(exc)['message']}", file=sys.stderr)
        return 1
    failures = sum(record["status"] != "success" for record in summary["records"])
    print(f"\nSaved {summary['completed_records']} records; {failures} failed. Comparisons not evaluated.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
