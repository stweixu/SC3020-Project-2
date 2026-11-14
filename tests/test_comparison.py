"""Tests for QEP tree comparison (Member 3)."""

from src.comparison import (
    compare_plans,
    format_comparison,
    identify_differences,
    iter_plan_nodes,
    match_nodes,
    normalize_condition,
    normalize_plan,
    tree_edit_distance,
)
from src.plan_node import PlanNode


def pg_scan(relation, node_type="Seq Scan", rows=1000, **extra):
    return PlanNode(node_type, {"Relation Name": relation, "Plan Rows": rows, **extra})


def ai_scan(relation, node_type="Seq Scan", rows=None, **extra):
    return PlanNode(node_type, {"relation_name": relation, "estimated_rows": rows, **extra})


def pg_customer_orders_join():
    return PlanNode(
        "Hash Join",
        {"Join Type": "Inner", "Hash Cond": "(o.o_custkey = c.c_custkey)",
         "Plan Rows": 300000, "Total Cost": 5000.0},
        [pg_scan("orders", rows=1500000),
         PlanNode("Hash", {}, [pg_scan("customer", rows=30000)])],
    )


def ai_customer_orders_join(join="Hash Join", inner_scan="Seq Scan"):
    return PlanNode(
        join,
        {"join_type": "Inner", "condition": "customer.c_custkey = orders.o_custkey"},
        [ai_scan("orders"), ai_scan("customer", inner_scan)],
    )


def types(differences):
    return [d["type"] for d in differences]


# --- match_nodes -----------------------------------------------------------


def test_match_nodes_pairs_corresponding_operators():
    result = match_nodes(pg_customer_orders_join(), ai_customer_orders_join())
    pairs = {(m["postgres"]["node_id"], m["ai"]["node_id"]) for m in result["matches"]}
    # join <-> join, orders <-> orders, customer (under Hash) <-> customer
    assert pairs == {("0", "0"), ("0.0", "0.0"), ("0.1.0", "0.1")}
    assert all(m["same_operator"] for m in result["matches"])
    assert [n["operator"] for n in result["unmatched_postgres"]] == ["Hash"]
    assert result["unmatched_ai"] == []


def test_match_nodes_matches_different_operators_of_same_kind():
    result = match_nodes(pg_customer_orders_join(),
                         ai_customer_orders_join("Nested Loop", "Index Scan"))
    by_pg = {m["postgres"]["node_id"]: m for m in result["matches"]}
    assert by_pg["0"]["ai"]["operator"] == "Nested Loop"
    assert by_pg["0"]["same_operator"] is False
    assert by_pg["0.1.0"]["ai"]["operator"] == "Index Scan"


def test_match_nodes_uses_alias_for_self_join():
    pg = PlanNode("Hash Join", {}, [
        pg_scan("nation", Alias="n1"),
        PlanNode("Hash", {}, [pg_scan("nation", Alias="n2")]),
    ])
    ai = PlanNode("Hash Join", {}, [
        ai_scan("nation", alias="n2"), ai_scan("nation", alias="n1"),
    ])
    result = match_nodes(pg, ai)
    pairs = {(m["postgres"]["node_id"], m["ai"]["node_id"]) for m in result["matches"]}
    assert ("0.0", "0.1") in pairs and ("0.1.0", "0.0") in pairs


# --- identify_differences ----------------------------------------------------


def test_identical_plans_have_no_differences():
    result = compare_plans(pg_customer_orders_join(), pg_customer_orders_join())
    assert result["identical"] is True
    assert result["differences"] == []
    assert result["tree_edit_distance"] == 0


def test_only_helper_node_differs_after_normalisation():
    diffs = identify_differences(pg_customer_orders_join(), ai_customer_orders_join())
    assert types(diffs) == ["auxiliary_operation"]
    assert diffs[0]["postgres_node_id"] == "0.1"


def test_scan_method_difference():
    pg = pg_scan("customer", "Index Scan", **{"Index Name": "customer_pkey"})
    diffs = identify_differences(pg, ai_scan("customer", "Sequential Scan"))
    assert types(diffs) == ["scan_method"]
    assert diffs[0]["postgres_value"] == "Index Scan"
    assert diffs[0]["ai_value"] == "Seq Scan"
    assert "statistics" in diffs[0]["explanation"]


def test_join_method_difference():
    diffs = identify_differences(pg_customer_orders_join(),
                                 ai_customer_orders_join("Nested Loop Join"))
    join = [d for d in diffs if d["type"] == "join_method"]
    assert len(join) == 1
    assert (join[0]["postgres_value"], join[0]["ai_value"]) == ("Hash Join", "Nested Loop")
    assert join[0]["postgres_node_id"] == "0" and join[0]["ai_node_id"] == "0"


def test_join_order_difference():
    pg = PlanNode("Hash Join", {}, [
        PlanNode("Hash Join", {}, [pg_scan("lineitem"), pg_scan("orders")]),
        pg_scan("customer"),
    ])
    ai = PlanNode("Hash Join", {}, [
        PlanNode("Hash Join", {}, [ai_scan("customer"), ai_scan("orders")]),
        ai_scan("lineitem"),
    ])
    order = [d for d in identify_differences(pg, ai) if d["type"] == "join_order"]
    assert len(order) == 1
    assert order[0]["postgres_value"] == "((lineitem ⋈ orders) ⋈ customer)"
    assert order[0]["ai_value"] == "((customer ⋈ orders) ⋈ lineitem)"


def test_swapped_join_inputs():
    pg = PlanNode("Hash Join", {}, [pg_scan("orders"), pg_scan("customer")])
    ai = PlanNode("Hash Join", {}, [ai_scan("customer"), ai_scan("orders")])
    assert types(identify_differences(pg, ai)) == ["join_input_order"]


def test_missing_and_additional_operations():
    pg = PlanNode("Limit", {}, [PlanNode("Hash Join", {}, [pg_scan("orders"), pg_scan("customer")])])
    ai = PlanNode("Sort", {}, [ai_scan("orders")])
    diffs = identify_differences(pg, ai)
    missing = {d["postgres_value"] for d in diffs if d["type"] == "missing_operation"}
    additional = {d["ai_value"] for d in diffs if d["type"] == "additional_operation"}
    assert missing == {"Limit", "Hash Join", "Seq Scan"}  # Seq Scan on customer
    assert additional == {"Sort"}
    customer = [d for d in diffs if d["postgres_node_id"] == "0.0.1"]
    assert "'customer'" in customer[0]["description"]


def test_aggregate_strategy_normalised_and_compared():
    pg = PlanNode("Aggregate", {"Strategy": "Hashed"}, [pg_scan("orders")])
    assert identify_differences(pg, PlanNode("HashAggregate", {}, [ai_scan("orders")])) == []

    ai = PlanNode("GroupAggregate", {}, [PlanNode("Sort", {}, [ai_scan("orders")])])
    assert sorted(types(identify_differences(pg, ai))) == ["additional_operation", "aggregate_method"]


def test_other_properties_join_type_index_condition_keys():
    pg = PlanNode("Nested Loop", {"Join Type": "Semi"}, [
        pg_scan("orders", "Index Scan", **{"Index Name": "orders_pkey",
                                          "Index Cond": "(o_orderkey = 5)"}),
        PlanNode("Sort", {"Sort Key": ["l_shipdate"]}, [pg_scan("lineitem")]),
    ])
    ai = PlanNode("Nested Loop", {"join_type": "Inner"}, [
        ai_scan("orders", "Index Scan", index_name="orders_custkey_idx",
                condition="o_custkey = 5"),
        PlanNode("Sort", {"sort_key": ["l_orderkey"]}, [ai_scan("lineitem")]),
    ])
    found = set(types(identify_differences(pg, ai)))
    assert {"join_type", "index_used", "condition", "sort_key"} <= found


def test_every_difference_has_description_and_explanation():
    pg = PlanNode("Limit", {}, [pg_customer_orders_join()])
    ai = PlanNode("Sort", {}, [ai_customer_orders_join("Merge Join", "Index Scan")])
    for d in identify_differences(pg, ai):
        assert d["description"] and d["explanation"]


# --- compare_plans -----------------------------------------------------------


def test_compare_plans_result_structure():
    result = compare_plans(pg_customer_orders_join(), ai_customer_orders_join("Merge Join"))
    for key in ("matches", "unmatched_postgres", "unmatched_ai", "differences",
                "difference_counts", "tree_edit_distance", "postgres_summary",
                "ai_summary", "estimates", "identical", "notes"):
        assert key in result
    assert "difference_rate" not in result  # Member 4's component
    assert result["difference_counts"] == {"join_method": 1, "auxiliary_operation": 1}
    assert result["tree_edit_distance"] == 0.5


def test_compare_plans_accepts_raw_json():
    raw_pg = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "customer",
                        "Plan Rows": 30000, "Total Cost": 5000.0}}]
    raw_ai = ('{"node_type": "Seq Scan", "properties": {"relation_name": "customer", '
              '"estimated_rows": 15000, "estimated_cost": null}, "children": []}')
    result = compare_plans(raw_pg, raw_ai)
    assert result["identical"] is True
    assert result["estimates"][0]["postgres_rows"] == 30000
    assert result["estimates"][0]["ai_rows"] == 15000


def test_node_ids_match_iter_plan_nodes():
    plan = pg_customer_orders_join()
    ids = dict(iter_plan_nodes(plan))
    result = match_nodes(plan, ai_customer_orders_join())
    for m in result["matches"]:
        assert ids[m["postgres"]["node_id"]].node_type == m["postgres"]["original_type"]


def test_bitmap_index_name_moves_to_heap_scan():
    pg = PlanNode("Bitmap Heap Scan", {"Relation Name": "orders"}, [
        PlanNode("Bitmap Index Scan", {"Index Name": "orders_date_idx"}),
    ])
    root = normalize_plan(pg)
    assert root.index == "orders_date_idx"
    assert root.children[0].category == "auxiliary"


# --- helpers ---------------------------------------------------------------


def test_generic_ai_scan_uses_scan_type_property():
    root = normalize_plan(PlanNode("Scan", {"scan_type": "Index Scan", "relation_name": "Orders"}))
    assert root.operator == "Index Scan"
    assert root.relation == "orders"


def test_tree_edit_distance():
    a = normalize_plan(PlanNode("Sort", {}, [pg_scan("orders")]))
    assert tree_edit_distance(a, normalize_plan(pg_scan("orders"))) == 1
    assert tree_edit_distance(a, normalize_plan(PlanNode("Sort", {}, [pg_scan("customer")]))) == 1
    assert tree_edit_distance(a, normalize_plan(PlanNode("Sort", {}, [pg_scan("orders", "Index Scan")]))) == 0.5


def test_normalize_condition():
    assert normalize_condition("((c.c_mktsegment)::text = 'BUILDING'::text)") == \
        normalize_condition("c_mktsegment = 'building'")
    assert normalize_condition("(o.o_custkey = c.c_custkey)") == \
        normalize_condition("customer.c_custkey = orders.o_custkey")


def test_format_comparison():
    text = format_comparison(compare_plans(pg_customer_orders_join(), pg_scan("orders")))
    assert "Matches:" in text and "Differences:" in text and "Why:" in text
