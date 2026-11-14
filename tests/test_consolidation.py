"""Recovery selection must preserve successes, failures and query identity."""

import json

import pytest

from collect_plans import write_json
from consolidate_results import consolidate
from evaluate_plans import evaluate_run
from test_evaluation import make_run, save_record
from src.project import load_saved_comparison


def evaluations(tmp_path, recovery_query="Q02", recovery_sql="SELECT * FROM widgets;", recovery_fails=False):
    initial = make_run(tmp_path / "initial", queries=("Q01", "Q02"))
    recovery = make_run(tmp_path / "recovery", queries=(recovery_query,))
    for query in ("Q01", "Q02"):
        save_record(initial, query, "postgres")
        save_record(initial, query, "ai", fail=query == "Q02")
    save_record(recovery, recovery_query, "postgres", sql=recovery_sql)
    save_record(recovery, recovery_query, "ai", operator="Index Scan", sql=recovery_sql, fail=recovery_fails)
    first, second = tmp_path / "first_eval", tmp_path / "recovery_eval"
    evaluate_run(initial, first)
    evaluate_run(recovery, second)
    return first, second


def test_recovery_consolidation_preserves_history_and_original_files(tmp_path):
    initial, recovery = evaluations(tmp_path)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    output = tmp_path / "combined"
    summary = consolidate(initial, recovery, output)
    assert summary["overall"]["valid_pairs"] == 2
    assert summary["overall"]["macro_difference_rate"]["mean"] == 50
    assert summary["collection_history"]["initial_failure_rate"] == 50
    assert summary["collection_history"]["recorded_pair_attempts"] == 3
    history = json.loads((output / "attempt_history.json").read_text())
    assert len(history["attempts"]) == 3
    assert history["attempts"][1]["collection_errors"]["ai"]["message"] == "Provider unavailable"
    assert load_saved_comparison(output / "pairs/Q02_001.json")["ai_plan"].node_type == "Index Scan"
    report = (output / "results.md").read_text()
    assert "Unresolved pair rate after recovery: 0.00%" in report
    assert "1 failed (50.00%)" in report
    assert all(path.read_bytes() == content for path, content in originals.items())
    with pytest.raises(FileExistsError):
        consolidate(initial, recovery, output)


@pytest.mark.parametrize("change", ["successful_query", "sql", "model", "comparator", "tampered_input"])
def test_incompatible_or_cherry_picked_recovery_is_rejected(tmp_path, change):
    initial, recovery = evaluations(
        tmp_path, recovery_query="Q01" if change == "successful_query" else "Q02",
        recovery_sql="SELECT * FROM widgets WHERE id = 1;" if change == "sql" else "SELECT * FROM widgets;",
    )
    if change in {"model", "comparator"}:
        path = recovery / "evaluation.json"
        metadata = json.loads(path.read_text())
        if change == "model":
            metadata["ai_run_metadata"]["llm"] = {"model": "different-model"}
        else:
            metadata["comparison_sha256"] = "different-code"
        write_json(path, metadata)
    elif change == "tampered_input":
        path = tmp_path / "recovery/records/Q02_ai_1.json"
        path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError):
        consolidate(initial, recovery, tmp_path / "combined")


def test_failed_recovery_remains_unavailable_and_is_kept_in_history(tmp_path):
    initial, recovery = evaluations(tmp_path, recovery_fails=True)
    summary = consolidate(initial, recovery, tmp_path / "combined")
    assert summary["overall"]["valid_pairs"] == 1
    assert summary["overall"]["failed_pairs"] == 1
    assert summary["collection_history"]["recovered_pairs"] == 0
