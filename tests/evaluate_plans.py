"""Evaluate saved plan collections offline and export report-ready tables."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, stdev
import sys
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from collect_plans import digest, error_details, plan_to_dict, restore_plan, utc_now, write_json
from src.comparison import DIFFERENCE_RATE_VERSION, classify_comparison, compare_plans


def load_run(directory):
    directory = Path(directory).resolve()
    metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    records = {}
    query_ids = metadata["query_ids"]
    repeats = metadata["repetitions"]
    if (not isinstance(query_ids, list) or not query_ids
            or any(not isinstance(q, str) or not q for q in query_ids)
            or len(set(query_ids)) != len(query_ids)
            or type(repeats) is not int or repeats < 1):
        raise ValueError("Invalid query IDs or repetition count in run metadata.")
    for path in sorted((directory / "records").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        key = (record["query_id"], record["source"], record["repetition"])
        if (key[0] not in query_ids or key[1] not in {"postgres", "ai"}
                or type(key[2]) is not int or not 1 <= key[2] <= repeats):
            raise ValueError(f"Record is outside the declared run: {path.name}")
        if key in records:
            raise ValueError(f"Duplicate plan record: {key}")
        records[key] = (record, path)
    return metadata, records


def check_run_compatibility(postgres, ai):
    """Reject cross-run comparisons whose recorded context/environment changed."""
    for key in ("schema_sha256", "preprocessing_sha256", "declared_dataset", "database"):
        if key not in postgres or key not in ai or postgres[key] != ai[key]:
            raise ValueError(f"Collections differ in {key}; recollect both sources together.")


def evaluate_pair(query_id, repetition, pg_entry, ai_entry):
    result = {
        "query_id": query_id, "repetition": repetition, "category": "unknown",
        "status": "missing_input", "difference_rate": None,
        "tree_edit_distance": None, "metric": None, "comparison_result": None,
        "postgres_plan": None, "ai_plan": None, "error": None, "inputs": {},
    }
    records = {}
    for side, entry in (("postgres", pg_entry), ("ai", ai_entry)):
        if entry is not None:
            record, path = entry
            records[side] = record
            result["category"] = record["category"]
            result["inputs"][side] = {
                "file": str(path), "sha256": digest(path.read_text(encoding="utf-8")),
                "status": record["status"], "repetition": record["repetition"],
            }
    if len(records) != 2:
        result["error"] = {"message": "A PostgreSQL or AI record is missing."}
        return result
    if any(record["status"] != "success" for record in records.values()):
        result["status"] = "input_failed"
        result["error"] = {"message": "At least one source failed collection; see original records."}
        return result
    try:
        pg, ai = records["postgres"], records["ai"]
        for record in (pg, ai):
            if record["sql_sha256"] != digest(record["sql"]):
                raise ValueError("A saved SQL hash does not match its SQL text.")
        if pg["sql"] != ai["sql"] or pg["category"] != ai["category"]:
            raise ValueError("Plan records differ in SQL or workload category.")
        postgres_plan, ai_plan = restore_plan(pg), restore_plan(ai)
        comparison = compare_plans(postgres_plan, ai_plan)
        metric = classify_comparison(comparison)
        result.update({
            "status": "success" if metric["difference_rate"] is not None else "unavailable",
            "sql": pg["sql"], "sql_sha256": pg["sql_sha256"],
            "difference_rate": metric["difference_rate"],
            "tree_edit_distance": comparison["tree_edit_distance"], "metric": metric,
            "comparison_result": comparison,
            "postgres_plan": plan_to_dict(postgres_plan), "ai_plan": plan_to_dict(ai_plan),
        })
    except Exception as exc:
        result["status"] = "comparison_failed"
        result["error"] = error_details(exc)
    return result


def statistics(values):
    return {"count": len(values), "mean": mean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "sample_stdev": stdev(values) if len(values) > 1 else None}


def summarise(results):
    by_query = defaultdict(list)
    difference_counts = Counter()
    affected_queries = defaultdict(set)
    for result in results:
        by_query[result["query_id"]].append(result)
        if result["status"] == "success":
            counts = Counter(d["type"] for d in result["comparison_result"]["differences"])
            difference_counts.update(counts)
            for kind in counts:
                affected_queries[kind].add(result["query_id"])
    queries = []
    for query_id, rows in sorted(by_query.items()):
        valid = [r for r in rows if r["status"] == "success"]
        categories = {r["category"] for r in rows if r["category"] != "unknown"}
        if len(categories) > 1:
            raise ValueError(f"Inconsistent categories across repetitions of {query_id}.")
        queries.append({
            "query_id": query_id, "category": next(iter(categories), "unknown"),
            "attempted": len(rows), "valid": len(valid), "failed": len(rows) - len(valid),
            "difference_rate": statistics([r["difference_rate"] for r in valid]),
            "tree_edit_distance": statistics([r["tree_edit_distance"] for r in valid]),
        })
    groups = defaultdict(list)
    for query in queries:
        groups[query["category"]].append(query)

    def group_summary(group):
        return {
            "queries": len(group), "valid_queries": sum(q["valid"] > 0 for q in group),
            "attempted_pairs": sum(q["attempted"] for q in group),
            "valid_pairs": sum(q["valid"] for q in group),
            "failed_pairs": sum(q["failed"] for q in group),
            "macro_difference_rate": statistics([q["difference_rate"]["mean"] for q in group if q["valid"]]),
            "macro_tree_edit_distance": statistics([q["tree_edit_distance"]["mean"] for q in group if q["valid"]]),
        }

    overall = group_summary(queries)
    overall["failure_rate"] = 100 * overall["failed_pairs"] / len(results) if results else None
    return {
        "method": DIFFERENCE_RATE_VERSION, "overall": overall, "queries": queries,
        "categories": {key: group_summary(group) for key, group in sorted(groups.items())},
        "status_counts": dict(Counter(r["status"] for r in results)),
        "difference_counts": dict(difference_counts),
        "queries_with_difference": {key: len(ids) for key, ids in sorted(affected_queries.items())},
    }


def write_tables(directory, results, summary):
    columns = ["query_id", "category", "repetition", "status", "difference_rate", "tree_edit_distance",
               "matched_pairs", "changed_pairs", "unmatched_postgres", "unmatched_ai",
               "different_units", "total_units", "auxiliary_findings"]
    with (directory / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for result in results:
            row = {key: result.get(key) for key in columns}
            row.update({key: (result["metric"] or {}).get(key) for key in columns[6:-1]})
            if result["comparison_result"] is not None:
                row["auxiliary_findings"] = result["comparison_result"]["difference_counts"].get("auxiliary_operation", 0)
            writer.writerow(row)
    with (directory / "queries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "category", "attempted", "valid", "failed", "mean_difference_rate",
                         "min_difference_rate", "max_difference_rate", "sample_stdev", "mean_tree_edit_distance"])
        for query in summary["queries"]:
            rate = query["difference_rate"]
            writer.writerow([query[key] for key in ("query_id", "category", "attempted", "valid", "failed")]
                            + [rate[key] for key in ("mean", "min", "max", "sample_stdev")]
                            + [query["tree_edit_distance"]["mean"]])

    def number(value):
        return "unavailable" if value is None else f"{value:.2f}"

    overall = summary["overall"]
    lines = ["# Offline comparison results", "",
             f"Method: `{DIFFERENCE_RATE_VERSION}`. Each query has equal weight after averaging its valid repetitions.", "",
             f"Valid pairs: {overall['valid_pairs']} / {overall['attempted_pairs']}. "
             f"Failure/unavailable rate: {number(overall['failure_rate'])}%.",
             f"Mean difference rate: {number(overall['macro_difference_rate']['mean'])}%.", "",
             "Tree-edit distance is a separate, unnormalised structural measurement. "
             "Neither measurement establishes correctness or speed. Failed pairs have no score.", "",
             "| Query | Category | Valid / attempted | Mean difference rate (%) | Mean tree-edit distance |",
             "|---|---|---:|---:|---:|"]
    for query in summary["queries"]:
        lines.append(f"| {query['query_id']} | {query['category']} | {query['valid']} / {query['attempted']} | "
                     f"{number(query['difference_rate']['mean'])} | {number(query['tree_edit_distance']['mean'])} |")
    lines += ["", "## Category summaries", "",
              "| Category | Valid queries / total | Mean difference rate (%) |", "|---|---:|---:|"]
    for category, group in summary["categories"].items():
        lines.append(f"| {category} | {group['valid_queries']} / {group['queries']} | "
                     f"{number(group['macro_difference_rate']['mean'])} |")
    lines += ["", "## Detected findings", "",
              "Finding totals are not the score numerator; multiple findings may affect the same pair.", "",
              "| Type | Findings | Queries affected |", "|---|---:|---:|"]
    for kind, count in sorted(summary["difference_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {kind} | {count} | {summary['queries_with_difference'][kind]} |")
    lines += ["", "## Examples to inspect", ""]
    valid = sorted((r for r in results if r["status"] == "success"), key=lambda r: r["difference_rate"])
    examples = [valid[0], valid[-1]] if len(valid) > 1 else valid
    for result in examples:
        lines.append(f"- {result['query_id']}, repetition {result['repetition']}: "
                     f"{number(result['difference_rate'])}%, TED {number(result['tree_edit_distance'])}; "
                     f"see [{result['file']}]({result['file']}).")
    lines += ["", "Inspect these plans before explaining the cause. Discuss condition normalisation, "
              "omitted properties, greedy matching, auxiliary nodes, and generation failures in the report."]
    (directory / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def evaluate_run(run_directory, output, postgres_directory=None, postgres_repeat=None):
    ai_meta, ai_records = load_run(run_directory)
    pg_meta, pg_records = load_run(postgres_directory or run_directory)
    if postgres_directory is not None:
        check_run_compatibility(pg_meta, ai_meta)
    if postgres_repeat is not None and not 1 <= postgres_repeat <= pg_meta["repetitions"]:
        raise ValueError("Requested PostgreSQL baseline repetition is outside its collection.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "pairs").mkdir()
    source = (PROJECT_ROOT / "src/comparison.py").read_text(encoding="utf-8")
    (output / "comparison_snapshot.py").write_text(source, encoding="utf-8")
    (output / "evaluation_snapshot.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    write_json(output / "evaluation.json", {
        "format_version": 1, "started_at": utc_now(), "method": DIFFERENCE_RATE_VERSION,
        "run": str(Path(run_directory).resolve()),
        "postgres_run": str(Path(postgres_directory or run_directory).resolve()),
        "postgres_repeat": postgres_repeat, "comparison_sha256": digest(source),
        "ai_run_metadata": ai_meta, "postgres_run_metadata": pg_meta,
    })
    write_json(output / "summary.json", {"evaluation_status": "in_progress"})
    results = []
    for index, query_id in enumerate(ai_meta["query_ids"], 1):
        for repetition in range(1, ai_meta["repetitions"] + 1):
            result = evaluate_pair(query_id, repetition,
                                   pg_records.get((query_id, "postgres", postgres_repeat or repetition)),
                                   ai_records.get((query_id, "ai", repetition)))
            result["file"] = f"pairs/{index:03d}_{repetition:03d}.json"
            write_json(output / result["file"], result)
            results.append(result)
    summary = summarise(results)
    summary.update({"evaluation_status": "complete", "finished_at": utc_now(),
                    "records": [{key: r[key] for key in ("query_id", "repetition", "status", "file")} for r in results]})
    write_tables(output, results, summary)
    write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Collection containing AI records (or both sources)")
    parser.add_argument("--postgres-run", type=Path, help="Separate, compatible PostgreSQL collection")
    parser.add_argument("--postgres-repeat", type=int, help="Explicitly reuse this PostgreSQL baseline for every AI repetition")
    parser.add_argument("--output", type=Path, help="New output directory; existing directories are never overwritten")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]
    output = args.output or PROJECT_ROOT / "artifacts/evaluations" / run_id
    try:
        summary = evaluate_run(args.run, output, args.postgres_run, args.postgres_repeat)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Evaluation failed: {error_details(exc)['message']}", file=sys.stderr)
        return 1
    print(f"Saved results to {output}\nValid pairs: {summary['overall']['valid_pairs']} / "
          f"{summary['overall']['attempted_pairs']}")
    return 1 if summary["overall"]["failed_pairs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
