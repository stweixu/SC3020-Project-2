"""Offline pairing, aggregation and provenance checks; no database/API needed."""

import csv
import json

import pytest

from collect_plans import collect_record, write_json
from evaluate_plans import evaluate_pair, evaluate_run, load_run
from src.plan_node import PlanNode
from src.project import load_saved_comparison


RAW = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "widgets"}}]


def make_run(path, repeats=1, queries=("Q01",)):
    path.mkdir()
    (path / "records").mkdir()
    write_json(path / "run.json", {
        "query_ids": list(queries), "repetitions": repeats,
        "schema_sha256": "schema", "preprocessing_sha256": "source",
        "declared_dataset": "synthetic unit-test fixture", "database": {"database": "fixture"},
    })
    return path


def save_record(run, query_id, side, repetition=1, operator="Seq Scan", fail=False, sql="SELECT * FROM widgets;"):
    def fetch(_):
        if fail:
            raise RuntimeError("Provider unavailable")
        return RAW if side == "postgres" else PlanNode(operator, {"relation_name": "widgets"})
    record = collect_record({"id": query_id, "category": "selection", "sql": sql}, side, repetition, fetch)
    path = run / "records" / f"{query_id}_{side}_{repetition}.json"
    write_json(path, record)
    return record, path


def test_macro_average_weights_queries_equally_and_preserves_failures(tmp_path):
    run = make_run(tmp_path / "run", repeats=2, queries=("Q01", "Q02", "Q03"))
    for query in ("Q01", "Q02"):
        for repetition in (1, 2):
            save_record(run, query, "postgres", repetition)
            save_record(run, query, "ai", repetition, operator="Index Scan" if query == "Q02" else "Seq Scan",
                        fail=query == "Q02" and repetition == 2)
    output = tmp_path / "evaluation"
    original = {p: p.read_bytes() for p in run.rglob("*.json")}
    summary = evaluate_run(run, output)
    assert summary["overall"]["macro_difference_rate"]["mean"] == 50
    assert summary["overall"]["valid_pairs"] == 3
    assert summary["overall"]["failure_rate"] == 50
    assert summary["status_counts"] == {"success": 3, "input_failed": 1, "missing_input": 2}
    assert summary["difference_counts"] == {"scan_method": 1}
    assert all(p.read_bytes() == content for p, content in original.items())
    rows = list(csv.DictReader((output / "results.csv").open()))
    assert rows[3]["difference_rate"] == ""
    assert rows[2]["tree_edit_distance"] == "0.5"
    assert (output / "comparison_snapshot.py").exists()
    assert "Mean difference rate: 50.00%" in (output / "results.md").read_text()
    loaded = load_saved_comparison(output / "pairs/002_001.json")
    assert loaded["postgres_plan"].node_type == "Seq Scan"
    assert loaded["ai_plan"].node_type == "Index Scan"
    with pytest.raises(ValueError, match="successful"):
        load_saved_comparison(output / "pairs/003_001.json")


def test_sql_mismatch_is_failure_not_disagreement(tmp_path):
    run = make_run(tmp_path / "run")
    pg = save_record(run, "Q01", "postgres")
    ai = save_record(run, "Q01", "ai", sql="SELECT * FROM other_table;")
    result = evaluate_pair("Q01", 1, pg, ai)
    assert result["status"] == "comparison_failed"
    assert result["difference_rate"] is None
    assert "SQL" in result["error"]["message"]


def test_cross_run_baseline_reuse_is_explicit_and_requires_same_environment(tmp_path):
    pg = make_run(tmp_path / "pg")
    ai = make_run(tmp_path / "ai", repeats=2)
    save_record(pg, "Q01", "postgres")
    save_record(ai, "Q01", "ai", 1)
    save_record(ai, "Q01", "ai", 2)
    missing = evaluate_run(ai, tmp_path / "no_reuse", pg)
    assert missing["overall"]["failed_pairs"] == 1
    reused = evaluate_run(ai, tmp_path / "reuse", pg, postgres_repeat=1)
    assert reused["overall"]["valid_pairs"] == 2
    metadata = json.loads((ai / "run.json").read_text())
    metadata["schema_sha256"] = "changed-schema"
    write_json(ai / "run.json", metadata)
    with pytest.raises(ValueError, match="schema_sha256"):
        evaluate_run(ai, tmp_path / "mismatch", pg)


def test_duplicate_records_and_existing_output_are_rejected(tmp_path):
    run = make_run(tmp_path / "run")
    record, _ = save_record(run, "Q01", "postgres")
    evaluate_run(run, tmp_path / "output")
    with pytest.raises(FileExistsError):
        evaluate_run(run, tmp_path / "output")
    write_json(run / "records/duplicate.json", record)
    with pytest.raises(ValueError, match="Duplicate"):
        load_run(run)


@pytest.mark.parametrize("record", [[], {"status": "success", "metric": None},
                                     {"status": "success", "metric": {"method": "unknown-version"}}])
def test_saved_result_rejects_invalid_or_incompatible_records(tmp_path, record):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        load_saved_comparison(path)
