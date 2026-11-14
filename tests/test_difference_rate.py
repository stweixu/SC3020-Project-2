"""Hand-calculated cases exercising the real comparator and metric together."""

from copy import deepcopy

import pytest

from src.comparison import calculate_difference_rate, classify_comparison, compare_plans
from src.plan_node import PlanNode


def scan(table="products", operator="Seq Scan", **properties):
    return PlanNode(operator, {"relation_name": table, **properties})


def test_identical_plans_and_estimates_only_score_zero():
    result = compare_plans(scan(estimated_rows=10, estimated_cost=3),
                           scan(estimated_rows=900, estimated_cost=200))
    before = deepcopy(result)
    assert calculate_difference_rate(result) == 0
    assert result == before


def test_multiple_findings_on_one_pair_count_once():
    pg = PlanNode("Limit", children=[scan(condition="price > 10")])
    ai = PlanNode("Limit", children=[scan(operator="Index Scan", condition="price > 20")])
    result = compare_plans(pg, ai)
    assert result["difference_counts"] == {"scan_method": 1, "condition": 1}
    metric = classify_comparison(result)
    assert (metric["changed_pairs"], metric["total_units"], metric["difference_rate"]) == (1, 2, 50)
    result["differences"].append(deepcopy(result["differences"][0]))
    assert calculate_difference_rate(result) == 50


def test_condition_difference_is_scored_even_with_zero_tree_distance():
    result = compare_plans(scan(condition="price > 10"), scan(condition="price > 20"))
    assert result["matches"][0]["same_operator"] is True
    assert result["tree_edit_distance"] == 0
    assert calculate_difference_rate(result) == 100


def test_missing_condition_is_unknown_under_current_comparator():
    result = compare_plans(scan(condition="price > 10"), scan())
    assert calculate_difference_rate(result) == 0  # no difference detected, not semantic equality


def test_auxiliary_and_additional_nodes_count_once_each():
    pg = PlanNode("Hash Join", children=[scan(), PlanNode("Hash", children=[scan("sales")])])
    ai = PlanNode("Hash Join", children=[scan(operator="Index Scan"),
                                        PlanNode("Sort", children=[scan("sales")])])
    result = compare_plans(pg, ai)
    metric = classify_comparison(result)
    assert (metric["matched_pairs"], metric["changed_pairs"]) == (3, 1)
    assert (metric["unmatched_postgres"], metric["unmatched_ai"]) == (1, 1)
    assert metric["difference_rate"] == 60
    assert metric["node_statuses"]["postgres"]["0.1"] == "postgres_only"
    assert metric["node_statuses"]["ai"]["0.1"] == "ai_only"


def test_swapped_inputs_mark_referenced_join_pair_not_both_scans():
    pg = PlanNode("Hash Join", children=[scan("products"), scan("sales")])
    ai = PlanNode("Hash Join", children=[scan("sales"), scan("products")])
    metric = classify_comparison(compare_plans(pg, ai))
    assert metric["difference_rate"] == pytest.approx(100 / 3)
    assert metric["node_statuses"]["postgres"] == {"0": "changed", "0.0": "unchanged", "0.1": "unchanged"}


def test_regrouped_join_marks_top_join_as_comparator_reports():
    pg = PlanNode("Hash Join", children=[PlanNode("Hash Join", children=[scan("a"), scan("b")]), scan("c")])
    ai = PlanNode("Hash Join", children=[scan("a"), PlanNode("Hash Join", children=[scan("b"), scan("c")])])
    result = compare_plans(pg, ai)
    assert result["difference_counts"] == {"join_order": 1}
    assert calculate_difference_rate(result) == 20


def test_no_matches_scores_one_hundred():
    metric = classify_comparison(compare_plans(scan("a"), scan("b")))
    assert (metric["matched_pairs"], metric["total_units"], metric["difference_rate"]) == (0, 2, 100)


def test_empty_units_have_no_score_and_absent_comparison_is_invalid():
    assert calculate_difference_rate({"matches": [], "unmatched_postgres": [],
                                      "unmatched_ai": [], "differences": []}) is None
    with pytest.raises(ValueError):
        calculate_difference_rate(None)


@pytest.mark.parametrize("damage", ["duplicate", "overlap", "unknown_ref", "no_ref", "unknown_type", "missing_node"])
def test_invalid_contract_never_silently_produces_score(damage):
    result = compare_plans(scan(condition="x > 1"), scan(condition="x > 2"))
    if damage == "duplicate":
        result["matches"].append(deepcopy(result["matches"][0]))
    elif damage == "overlap":
        result["unmatched_ai"].append(result["matches"][0]["ai"])
    elif damage == "unknown_ref":
        result["differences"][0]["ai_node_id"] = "0.99"
    elif damage == "no_ref":
        result["differences"][0].update(postgres_node_id=None, ai_node_id=None)
    elif damage == "unknown_type":
        result["differences"][0]["type"] = "future_difference"
    else:
        result["matches"] = []
    with pytest.raises(ValueError):
        calculate_difference_rate(result)
