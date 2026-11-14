"""Collection preserves inputs and failures without depending on a comparator."""

import json

import pytest

from collect_plans import collect_batch, collect_record, restore_plan
from src.plan_node import PlanNode


QUERY = {"id": "Q01", "category": "selection", "sql": "SELECT 1;"}
RAW_PLAN = [{"Plan": {"Node Type": "Result", "Total Cost": 0.01}}]


def test_postgres_record_round_trips_without_comparison():
    record = collect_record(QUERY, "postgres", 1, lambda sql: RAW_PLAN)
    saved = json.loads(json.dumps(record))
    assert saved["raw_plan"] == RAW_PLAN
    assert saved["comparison_status"] == "not_evaluated"
    assert saved["difference_rate"] is None
    assert restore_plan(saved).properties["Total Cost"] == 0.01


def test_ai_capture_is_explicitly_parsed_only():
    root = PlanNode("Hash Join", children=[
        PlanNode("Seq Scan", {"relation_name": "items", "alias": "a"}),
        PlanNode("Seq Scan", {"relation_name": "items", "alias": "b"}),
    ])
    record = collect_record(QUERY, "ai", 2, lambda sql: root)
    assert record["status"] == "success"
    assert record["raw_plan"] is None
    assert not record["raw_response_available"]
    restored = restore_plan(json.loads(json.dumps(record)))
    assert [child.properties["alias"] for child in restored.children] == ["a", "b"]


def test_invalid_raw_plan_is_preserved_for_diagnosis():
    raw = [{"Plan": {"Plans": []}}]
    record = collect_record(QUERY, "postgres", 1, lambda sql: raw)
    assert record["status"] == "parsing_failed"
    assert record["raw_plan"] == raw
    assert record["plan"] is None
    with pytest.raises(ValueError, match="successful"):
        restore_plan(record)


def test_batch_continues_after_failure_and_checkpoints_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-for-storage")

    def fetch(sql):
        if sql == QUERY["sql"]:
            raise RuntimeError("Request rejected: test-key-not-for-storage")
        return RAW_PLAN

    second = {**QUERY, "id": "Q02", "sql": "SELECT 2;"}
    summary = collect_batch(tmp_path, [QUERY, second], ["postgres"], 1, {"postgres": fetch})
    assert summary["collection_status"] == "complete"
    assert summary["counts"] == {"postgres:retrieval_failed": 1, "postgres:success": 1}
    assert json.loads((tmp_path / "summary.json").read_text()) == summary
    failure = (tmp_path / "records/Q01_postgres_01.json").read_text()
    assert "test-key-not-for-storage" not in failure
    assert "[redacted]" in failure
    success = json.loads((tmp_path / "records/Q02_postgres_01.json").read_text())
    assert success["status"] == "success"


def test_repetitions_have_separate_records(tmp_path):
    summary = collect_batch(tmp_path, [QUERY], ["postgres"], 2, {"postgres": lambda sql: RAW_PLAN})
    assert summary["completed_records"] == 2
    assert len(list((tmp_path / "records").glob("*.json"))) == 2
    assert {record["repetition"] for record in summary["records"]} == {1, 2}


def test_ai_failure_does_not_discard_postgres_record(tmp_path):
    def fail_ai(sql):
        raise RuntimeError("Provider unavailable")

    summary = collect_batch(tmp_path, [QUERY], ["postgres", "ai"], 1, {
        "postgres": lambda sql: RAW_PLAN, "ai": fail_ai,
    })
    assert summary["counts"] == {"postgres:success": 1, "ai:generation_failed": 1}
    saved = json.loads((tmp_path / "records/Q01_postgres_01.json").read_text())
    assert restore_plan(saved).node_type == "Result"


def test_interrupted_batch_retains_completed_records(tmp_path):
    def fetch(sql):
        if sql == "SELECT 2;":
            raise KeyboardInterrupt
        return RAW_PLAN

    second = {**QUERY, "id": "Q02", "sql": "SELECT 2;"}
    with pytest.raises(KeyboardInterrupt):
        collect_batch(tmp_path, [QUERY, second], ["postgres"], 1, {"postgres": fetch})
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["collection_status"] == "in_progress"
    assert summary["completed_records"] == 1
    assert (tmp_path / "records/Q01_postgres_01.json").exists()
