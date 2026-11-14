"""Tests for QEP tree comparison."""

from src.comparison import (
    compare_plans,
    format_comparison,
    normalize_condition,
    normalize_plan,
    tree_edit_distance,
)
from src.plan_node import PlanNode


def pg_scan(relation, node_type="Seq Scan", rows=1000, **extra):
    return PlanNode(node_type, {"Relation Name": relation, "Plan Rows": rows, **extra})


def ai_scan(relation, node_type="Seq Scan", rows=None):
    return PlanNode(node_type, {"relation_name": relation, "estimated_rows": rows})


def pg_customer_orders_join():
    return PlanNode(
        "Hash Join",
        {"Join Type": "Inner", "Hash Cond": "(o.o_custkey = c.c_custkey)",
         "Plan Rows": 300000, "Total Cost": 5000.0},
        [pg_scan("orders", rows=1500000),
         PlanNode("Hash", {}, [pg_scan("customer", rows=30000)])],
    )


def test_identical_plans_have_zero_difference():
    result = compare_plans(pg_customer_orders_join(), pg_customer_orders_join())
    assert result["difference_rate"] == 0.0
    assert result["similarity"] == 1.0
    assert result["identical_structure"] is True
    assert result["differences"] == []


def test_hash_node_ignored_and_ai_vocabulary_normalised():
    ai_plan = PlanNode(
        "Hash Join",
        {"join_type": "Inner", "condition": "customer.c_custkey = orders.o_custkey"},
        [ai_scan("orders"), ai_scan("customer", "Sequential Scan")],
    )
    result = compare_plans(pg_customer_orders_join(), ai_plan)
    assert result["difference_rate"] == 0.0
    categories = [d["category"] for d in result["differences"]]
    # Only the omitted Hash helper node is reported.
    assert categories == ["auxiliary"]


def test_different_scan_method_is_reported():
    pg_plan = pg_scan("customer", "Index Scan", **{"Index Name": "customer_pkey"})
    ai_plan = ai_scan("customer", "Seq Scan")
    result = compare_plans(pg_plan, ai_plan)
    assert result["difference_rate"] == 0.5
    scan_diffs = [d for d in result["differences"] if d["category"] == "scan"]
    assert len(scan_diffs) == 1
    assert scan_diffs[0]["postgres"] == ["Index Scan"]
    assert scan_diffs[0]["ai"] == ["Seq Scan"]


def test_different_join_algorithm_is_reported():
    ai_plan = PlanNode(
        "Nested Loop Join", {"join_type": "Inner"},
        [ai_scan("orders"), ai_scan("customer", "Index Scan")],
    )
    result = compare_plans(pg_customer_orders_join(), ai_plan)
    join_diffs = [d for d in result["differences"] if d["category"] == "join"]
    assert join_diffs[0]["postgres"] == "Hash Join"
    assert join_diffs[0]["ai"] == "Nested Loop"
    assert 0 < result["difference_rate"] < 1


def test_join_order_difference():
    pg_plan = PlanNode("Hash Join", {}, [
        PlanNode("Hash Join", {}, [pg_scan("lineitem"), pg_scan("orders")]),
        pg_scan("customer"),
    ])
    ai_plan = PlanNode("Hash Join", {}, [
        PlanNode("Hash Join", {}, [ai_scan("customer"), ai_scan("orders")]),
        ai_scan("lineitem"),
    ])
    result = compare_plans(pg_plan, ai_plan)
    order = [d for d in result["differences"] if d["category"] == "join_order"]
    assert len(order) == 1
    assert order[0]["postgres"] == "((lineitem ⋈ orders) ⋈ customer)"
    assert order[0]["ai"] == "((customer ⋈ orders) ⋈ lineitem)"


def test_swapped_join_inputs_are_noted_separately():
    pg_plan = PlanNode("Hash Join", {}, [pg_scan("orders"), pg_scan("customer")])
    ai_plan = PlanNode("Hash Join", {}, [ai_scan("customer"), ai_scan("orders")])
    result = compare_plans(pg_plan, ai_plan)
    order = [d for d in result["differences"] if d["category"] == "join_order"]
    assert len(order) == 1
    assert "swapped" in order[0]["description"]


def test_postgres_aggregate_strategy_matches_ai_hashaggregate():
    pg_plan = PlanNode("Aggregate", {"Strategy": "Hashed"}, [pg_scan("orders")])
    ai_plan = PlanNode("HashAggregate", {}, [ai_scan("orders")])
    result = compare_plans(pg_plan, ai_plan)
    assert result["difference_rate"] == 0.0

    ai_sorted = PlanNode("GroupAggregate", {}, [PlanNode("Sort", {}, [ai_scan("orders")])])
    result = compare_plans(pg_plan, ai_sorted)
    categories = {d["category"] for d in result["differences"]}
    assert {"root", "aggregate", "sort"} <= categories


def test_missing_relation_is_reported():
    pg_plan = PlanNode("Hash Join", {}, [pg_scan("orders"), pg_scan("customer")])
    ai_plan = ai_scan("orders")
    result = compare_plans(pg_plan, ai_plan)
    descriptions = " ".join(d["description"] for d in result["differences"])
    assert "'customer'" in descriptions
    assert "does not access it" in descriptions


def test_accepts_raw_postgres_json_and_raw_ai_json():
    raw_pg = [{"Plan": {
        "Node Type": "Seq Scan", "Relation Name": "customer",
        "Plan Rows": 30000, "Total Cost": 5000.0,
    }}]
    raw_ai = (
        '{"node_type": "Seq Scan", "properties": {"relation_name": "customer", '
        '"estimated_rows": 15000, "estimated_cost": null}, "children": []}'
    )
    result = compare_plans(raw_pg, raw_ai)
    assert result["difference_rate"] == 0.0
    root_rows = result["estimates"][0]
    assert root_rows["postgres"] == 30000
    assert root_rows["ai"] == 15000
    assert root_rows["ai_to_postgres_ratio"] == 0.5


def test_generic_ai_scan_uses_scan_type_property():
    nodes = normalize_plan(PlanNode("Scan", {"scan_type": "Index Scan", "relation_name": "Orders"}))
    assert nodes[0].operator == "Index Scan"
    assert nodes[0].relation == "orders"


def test_auxiliary_nodes_can_be_included():
    ai_plan = PlanNode("Hash Join", {}, [ai_scan("orders"), ai_scan("customer")])
    result = compare_plans(pg_customer_orders_join(), ai_plan, ignore_auxiliary=False)
    assert result["tree_edit_distance"] == 1
    assert result["postgres_summary"]["node_count"] == 4


def test_tree_edit_distance_insert_and_relabel():
    a = normalize_plan(PlanNode("Sort", {}, [pg_scan("orders")]))[0]
    b = normalize_plan(pg_scan("orders"))[0]
    assert tree_edit_distance(a, b) == 1
    c = normalize_plan(PlanNode("Sort", {}, [pg_scan("customer")]))[0]
    assert tree_edit_distance(a, c) == 1


def test_normalize_condition_ignores_casts_qualifiers_and_order():
    pg = "((c.c_mktsegment)::text = 'BUILDING'::text)"
    ai = "c_mktsegment = 'building'"
    assert normalize_condition(pg) == normalize_condition(ai)
    assert normalize_condition("(o.o_custkey = c.c_custkey)") == normalize_condition(
        "customer.c_custkey = orders.o_custkey"
    )


def test_format_comparison_produces_text():
    text = format_comparison(compare_plans(pg_customer_orders_join(), pg_scan("orders")))
    assert text.startswith("Difference rate:")
    assert "Differences:" in text
