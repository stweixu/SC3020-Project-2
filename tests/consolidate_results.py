"""Combine an initial evaluation and a recovery run without replacing successes."""

import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import re

from collect_plans import digest, utc_now, write_json
from evaluate_plans import check_run_compatibility, summarise, write_tables
from src.comparison import DIFFERENCE_RATE_VERSION, classify_comparison


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_evaluation(directory):
    directory = Path(directory).resolve()
    metadata = read_json(directory / "evaluation.json")
    summary = read_json(directory / "summary.json")
    if summary.get("evaluation_status") != "complete":
        raise ValueError("Only completed evaluations can be consolidated.")
    if metadata["method"] != DIFFERENCE_RATE_VERSION:
        raise ValueError("Unsupported metric version.")
    pairs = {}
    for entry in summary["records"]:
        path = (directory / entry["file"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Pair path is outside the evaluation folder.")
        record = read_json(path)
        key = (record["query_id"], record["repetition"])
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", key[0])
                or type(key[1]) is not int or key[1] < 1 or key in pairs):
            raise ValueError("Invalid or duplicate query/repetition in evaluation.")
        if any(record[field] != entry[field] for field in ("query_id", "repetition", "status")):
            raise ValueError("Evaluation summary and pair record disagree.")
        # Read original inputs even for failed pairs, which have no top-level SQL.
        inputs = {}
        for side in ("postgres", "ai"):
            source = record["inputs"][side]
            text = Path(source["file"]).read_text(encoding="utf-8")
            if digest(text) != source["sha256"]:
                raise ValueError("Original collection record changed after evaluation.")
            value = json.loads(text)
            if (value["query_id"], value["repetition"], value["source"]) != (*key, side):
                raise ValueError("Collection record identity does not match its evaluated pair.")
            if value["sql_sha256"] != digest(value["sql"]):
                raise ValueError("Invalid SQL hash in collection record.")
            inputs[side] = value
        if inputs["postgres"]["sql"] != inputs["ai"]["sql"]:
            raise ValueError("PostgreSQL and AI SQL differ.")
        if record["status"] == "success":
            metric = classify_comparison(record["comparison_result"])
            if metric != record["metric"] or metric["difference_rate"] != record["difference_rate"]:
                raise ValueError("Saved metric does not match its comparison findings.")
            if record["tree_edit_distance"] != record["comparison_result"]["tree_edit_distance"]:
                raise ValueError("Saved tree-edit distance does not match its comparison.")
        pairs[key] = {
            "record": record, "sql": inputs["postgres"]["sql"],
            "source_file": str(path), "sha256": digest(path.read_text(encoding="utf-8")),
            "collection_errors": {side: value.get("error") for side, value in inputs.items()},
        }
    expected = {(query, repeat) for query in metadata["ai_run_metadata"]["query_ids"]
                for repeat in range(1, metadata["ai_run_metadata"]["repetitions"] + 1)}
    if set(pairs) != expected:
        raise ValueError("Evaluation does not cover its declared queries/repetitions.")
    return metadata, pairs


def consolidate(original_directory, recovery_directory, output):
    original_meta, original = load_evaluation(original_directory)
    recovery_meta, recovery = load_evaluation(recovery_directory)
    for key in ("method", "comparison_sha256", "postgres_repeat"):
        if original_meta[key] != recovery_meta[key]:
            raise ValueError(f"Evaluations differ in {key}.")
    for side in ("ai_run_metadata", "postgres_run_metadata"):
        check_run_compatibility(original_meta[side], recovery_meta[side])
        if original_meta[side].get("llm") != recovery_meta[side].get("llm"):
            raise ValueError("Model/settings changed between the initial and recovery runs.")
    for key, recovered in recovery.items():
        if key not in original:
            raise ValueError("Recovery contains a query/repetition outside the initial experiment.")
        if original[key]["record"]["status"] == "success":
            raise ValueError("Recovery must not replace an initially successful pair.")
        if (original[key]["sql"] != recovered["sql"]
                or original[key]["record"]["category"] != recovered["record"]["category"]):
            raise ValueError("Recovery SQL/category differs from the original pair.")

    selected = []
    for key, initial in sorted(original.items()):
        use_recovery = key in recovery and recovery[key]["record"]["status"] == "success"
        chosen = recovery[key] if use_recovery else initial
        record = deepcopy(chosen["record"])
        record["selection_source"] = "recovery" if use_recovery else "initial"
        record["source_evaluation_file"] = chosen["source_file"]
        record["source_evaluation_sha256"] = chosen["sha256"]
        record["initial_status"] = initial["record"]["status"]
        record["file"] = f"pairs/{key[0]}_{key[1]:03d}.json"
        selected.append(record)

    initial_failed = sum(item["record"]["status"] != "success" for item in original.values())
    recovered_count = sum(record["selection_source"] == "recovery" for record in selected)
    history = {
        "initial_pairs": len(original), "initial_failed_pairs": initial_failed,
        "initial_failure_rate": 100 * initial_failed / len(original) if original else None,
        "recovery_pairs": len(recovery), "recovered_pairs": recovered_count,
        "recorded_pair_attempts": len(original) + len(recovery),
        "policy": "Keep initial successes; use successful recovery only for failed initial pairs. Pilots excluded.",
        "initial_status_counts": dict(Counter(v["record"]["status"] for v in original.values())),
        "recovery_status_counts": dict(Counter(v["record"]["status"] for v in recovery.values())),
        "attempts": [{"stage": stage, **item} for stage, values in (("initial", original), ("recovery", recovery))
                     for item in values.values()],
    }
    summary = summarise(selected)
    summary["summary_scope"] = "Selected results after recovery, not all collection attempts"
    summary["collection_history"] = {key: value for key, value in history.items() if key != "attempts"}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "pairs").mkdir()
    for record in selected:
        write_json(output / record["file"], record)
    write_tables(output, selected, summary)
    # The readable report distinguishes eventual coverage from initial reliability.
    report = (output / "results.md").read_text(encoding="utf-8")
    report = report.replace("# Offline comparison results", "# Consolidated experiment results", 1)
    report = report.replace("Failure/unavailable rate:", "Unresolved pair rate after recovery:", 1)
    report = report.replace("| Query | Category | Valid / attempted |", "| Query | Category | Valid / selected |", 1)
    history_text = (
        f"## Collection history\n\n"
        f"- Initial batch: {len(original) - initial_failed}/{len(original)} pairs successful; "
        f"{initial_failed} failed ({history['initial_failure_rate']:.2f}%).\n"
        f"- Recovery: {recovered_count}/{len(recovery)} pairs successfully recovered.\n"
        f"- Recorded pair attempts across these two runs: {len(original) + len(recovery)} "
        "(not a count of SDK-level retries).\n"
        "- Each query/repetition contributes at most one selected score. Initial successes are retained.\n"
        "- Pilots are excluded. Original failures and provider messages are retained in `attempt_history.json`.\n"
        "- The tables below describe the selected results after recovery; they do not imply failure-free collection.\n\n"
    )
    report = report.replace("## Category summaries", history_text + "## Category summaries", 1)
    report += "\n\n## Result sources\n\n| Query | Repetition | Selected source | Saved pair |\n|---|---:|---|---|\n"
    for record in selected:
        report += (f"| {record['query_id']} | {record['repetition']} | {record['selection_source']} | "
                   f"[Open JSON]({record['file']}) |\n")
    (output / "results.md").write_text(report, encoding="utf-8")
    with (output / "selection.csv").open("w", encoding="utf-8", newline="") as handle:
        columns = ["query_id", "repetition", "initial_status", "selection_source", "status",
                   "difference_rate", "tree_edit_distance", "source_evaluation_file"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: record[key] for key in columns} for record in selected)
    write_json(output / "attempt_history.json", history)
    write_json(output / "summary.json", summary)
    write_json(output / "consolidation.json", {
        "created_at": utc_now(), "initial_evaluation": str(Path(original_directory).resolve()),
        "recovery_evaluation": str(Path(recovery_directory).resolve()),
        "initial_metadata": original_meta, "recovery_metadata": recovery_meta,
        "policy": history["policy"],
    })
    (output / "consolidation_snapshot.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("initial", type=Path)
    parser.add_argument("recovery", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    try:
        summary = consolidate(args.initial, args.recovery, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Consolidation failed: {exc}\n")
    print(f"Saved consolidated table to {args.output / 'results.md'}")
    print(f"Valid selected pairs: {summary['overall']['valid_pairs']} / {summary['overall']['attempted_pairs']}")
    return 1 if summary["overall"]["failed_pairs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
