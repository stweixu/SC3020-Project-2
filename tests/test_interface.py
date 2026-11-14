"""Presentation contract and desktop interaction checks; no API calls."""

import threading
import time
import tkinter as tk

import pytest

from src.interface import ComparisonApp, adapt_comparison_result, demo_comparison, index_plan
from src.plan_node import PlanNode
from src import project
from src.comparison import compare_plans


def test_paths_identify_repeated_operators_and_relations():
    root = PlanNode("Nested Loop", children=[
        PlanNode("Seq Scan", {"relation_name": "items", "alias": "a"}),
        PlanNode("Seq Scan", {"relation_name": "items", "alias": "b"}),
    ])
    nodes = index_plan(root)
    assert list(nodes) == ["0", "0.0", "0.1"]
    assert nodes["0.0"].properties["alias"] == "a"
    assert nodes["0.1"].properties["alias"] == "b"


def test_cyclic_plan_is_rejected():
    root = PlanNode("Sort")
    root.children.append(root)
    with pytest.raises(ValueError, match="cycles"):
        index_plan(root)


def test_absent_comparison_does_not_imply_equal_plans():
    view = adapt_comparison_result(None, {"0"}, {"0"})
    assert view["difference_rate"] is None
    assert view["node_statuses"] == {"postgres": {}, "ai": {}}
    assert view["findings"] == []


@pytest.mark.parametrize("rate", [-1, 101, float("nan"), float("inf"), True, "50%"])
def test_invalid_percentages_are_rejected(rate):
    with pytest.raises(ValueError, match="difference_rate"):
        adapt_comparison_result({"difference_rate": rate}, {"0"}, {"0"})


def test_unknown_finding_reference_is_rejected():
    with pytest.raises(ValueError, match="unknown ai node"):
        adapt_comparison_result({"findings": [{
            "explanation": "Changed scan", "ai_node_ids": ["missing"],
        }]}, {"0"}, {"0"})


def test_malformed_status_is_rejected():
    with pytest.raises(ValueError, match="Invalid postgres node status"):
        adapt_comparison_result(
            {"node_statuses": {"postgres": {"0": ["changed"]}}}, {"0"}, {"0"}
        )


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root):
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()


def test_demo_selection_zoom_and_result_reset(window):
    app = ComparisonApp(window)
    app.load_demo()
    assert "DEMO" in app.status.get()
    assert "60.0%" in app.rate.get()
    assert len(app.postgres_tree.rectangles) == 4
    assert len(app.ai_tree.rectangles) == 4
    assert app.postgres_tree.positions["0.0"][0] < app.postgres_tree.positions["0.1"][0]
    app.finding_list.selection_set("0")
    app.select_finding()
    assert app.postgres_tree.selected == {"0.0"}
    assert app.ai_tree.selected == {"0.0"}
    app.ai_tree.set_zoom(0.5)
    assert app.ai_tree.selected == {"0.0"}
    app.select_node("postgres", "0.1")
    assert "Hash" in app.details.get("1.0", "end")
    pg, _, _ = demo_comparison()
    app.show_result(pg, None, None, "PostgreSQL only")
    assert not app.ai_tree.nodes
    assert not app.finding_list.get_children()
    assert app.postgres_tree.statuses == {}
    assert "unavailable" in app.rate.get()


def test_bad_comparison_preserves_neutral_plans(window):
    app = ComparisonApp(window)
    pg, ai, _ = demo_comparison()
    app.show_result(pg, ai, {"node_statuses": {"ai": {"missing": "changed"}}})
    assert len(app.ai_tree.nodes) == 4
    assert app.ai_tree.statuses == {}
    assert "needs attention" in app.status.get()
    assert "unavailable" in app.rate.get()


@pytest.mark.parametrize("fail", [False, True])
def test_worker_completes_without_blocking_tk(window, fail):
    thread_ids = []

    def retrieve(sql, schema, mode):
        thread_ids.append(threading.get_ident())
        assert sql == "SELECT 1"
        assert mode == "postgres"
        if fail:
            raise RuntimeError("Connection unavailable")
        return {"postgres_plan": PlanNode("Result"), "notice": "Fetched"}

    app = ComparisonApp(window, retrieve)
    app.sql.insert("1.0", "SELECT 1")
    app.run()
    assert app.busy
    deadline = time.monotonic() + 3
    while app.busy and time.monotonic() < deadline:
        window.update()
        time.sleep(0.01)
    assert not app.busy
    assert thread_ids[0] != threading.get_ident()
    if fail:
        assert "Connection unavailable" in app.details.get("1.0", "end")
        assert not app.postgres_tree.nodes
    else:
        assert app.postgres_tree.nodes["0"].node_type == "Result"


def test_postgres_mode_parses_json_without_calling_ai(monkeypatch):
    monkeypatch.setattr(project, "get_postgres_plan", lambda sql: [{"Plan": {"Node Type": "Result"}}])

    def unexpected_ai(*args):
        pytest.fail("PostgreSQL-only mode must not call the AI API")

    monkeypatch.setattr(project, "get_ai_plan", unexpected_ai)
    result = project.run_query("SELECT 1", display=False, mode="postgres")
    assert isinstance(result["postgres_plan"], PlanNode)
    assert result["ai_plan"] is None
    assert result["comparison_result"] is None


def test_missing_comparator_still_returns_both_plans(monkeypatch):
    monkeypatch.setattr(project, "get_postgres_plan", lambda sql: [{"Plan": {"Node Type": "Result"}}])
    monkeypatch.setattr(project, "get_ai_plan", lambda *args: PlanNode("Result"))

    def pending(*args):
        raise NotImplementedError

    monkeypatch.setattr(project, "compare_plans", pending)
    result = project.run_query("SELECT 1", "example context", display=False)
    assert result["ai_plan"].node_type == "Result"
    assert result["comparison_result"] is None
    assert "not implemented" in result["notice"]


def test_ai_failure_preserves_postgres_plan(monkeypatch):
    monkeypatch.setattr(project, "get_postgres_plan", lambda sql: [{"Plan": {"Node Type": "Result"}}])

    def unavailable(*args):
        raise project.AIPlanError("API unavailable")

    monkeypatch.setattr(project, "get_ai_plan", unavailable)
    result = project.run_query("SELECT 1", "example context", display=False)
    assert result["postgres_plan"].node_type == "Result"
    assert result["ai_plan"] is None
    assert "API unavailable" in result["notice"]


def test_actual_comparator_condition_changes_colour_and_score(window):
    pg = PlanNode("Seq Scan", {"Relation Name": "inventory", "Filter": "quantity > 10"})
    ai = PlanNode("Sequential Scan", {"relation_name": "inventory", "condition": "quantity > 20"})
    result = compare_plans(pg, ai)
    result["difference_rate"] = 0  # stale cached percentages must not override the shared rules
    app = ComparisonApp(window)
    app.show_result(pg, ai, result)
    assert "100.0%" in app.rate.get()
    assert app.postgres_tree.statuses == app.ai_tree.statuses == {"0": "changed"}
    app.finding_list.selection_set("0")
    app.select_finding()
    assert app.postgres_tree.selected == app.ai_tree.selected == {"0"}
    assert "quantity" in app.details.get("1.0", "end")


def test_similarities_link_matched_nodes_at_different_paths():
    pg = PlanNode("Hash", children=[PlanNode("Seq Scan", {"Relation Name": "inventory"})])
    ai = PlanNode("Seq Scan", {"relation_name": "inventory"})
    view = adapt_comparison_result(compare_plans(pg, ai), index_plan(pg), index_plan(ai))
    similarity = next(f for f in view["findings"] if f["category"] == "Similarity")
    assert similarity["postgres_node_ids"] == ["0.0"]
    assert similarity["ai_node_ids"] == ["0"]
    assert view["difference_rate"] == 50


def test_orchestration_uses_live_context_and_calculates_metric(monkeypatch):
    monkeypatch.setattr(project, "get_postgres_plan", lambda sql: [{"Plan": {"Node Type": "Result"}}])
    monkeypatch.setattr(project, "get_database_context", lambda: "custom schema context")

    def ai(sql, schema):
        assert schema == "custom schema context"
        return PlanNode("Result")

    monkeypatch.setattr(project, "get_ai_plan", ai)
    result = project.run_query("SELECT 1", display=False)
    assert result["comparison_result"]["difference_rate"] == 0
    assert result["comparison_result"]["metric"]["total_units"] == 1


def test_metric_failure_retains_both_plans_without_a_score(monkeypatch):
    monkeypatch.setattr(project, "get_postgres_plan", lambda sql: [{"Plan": {"Node Type": "Result"}}])
    monkeypatch.setattr(project, "get_ai_plan", lambda *args: PlanNode("Result"))
    monkeypatch.setattr(project, "compare_plans", lambda *args: {"matches": []})
    result = project.run_query("SELECT 1", "context", display=False)
    assert result["postgres_plan"] and result["ai_plan"]
    assert result["comparison_result"] is None
    assert "comparison failed" in result["notice"]


def test_open_saved_result_and_failed_reload_clear_stale_results(window, monkeypatch):
    from src import interface

    plan = PlanNode("Result")
    result = {"postgres_plan": plan, "ai_plan": plan, "comparison_result": compare_plans(plan, plan),
              "sql": "SELECT 1", "notice": "SAVED RESULT — controlled fixture"}
    app = ComparisonApp(window, load_callback=lambda path: result)
    monkeypatch.setattr(interface.filedialog, "askopenfilename", lambda **kwargs: "fixture.json")
    app.open_saved()
    assert app.sql.get("1.0", "end").strip() == "SELECT 1"
    assert "0.0%" in app.rate.get()
    assert "SAVED RESULT" in app.status.get()

    def fail(path):
        raise ValueError("Invalid saved pair")

    app.load_callback = fail
    app.open_saved()
    assert not app.postgres_tree.nodes and not app.ai_tree.nodes
    assert "unavailable" in app.rate.get()
    assert "Invalid saved pair" in app.details.get("1.0", "end")
