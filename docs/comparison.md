# QEP comparison (Member 3)

Implemented in `src/comparison.py`. Tests: `tests/test_comparison.py`
(`python -m pytest tests/test_comparison.py`; no database or API key needed).

## Functions

| Function | Input | Output |
| --- | --- | --- |
| `compare_plans(postgres_plan, ai_plan)` | both plans | `comparison_result` dictionary (below) |
| `match_nodes(postgres_plan, ai_plan)` | both plans | `{matches, unmatched_postgres, unmatched_ai}` |
| `identify_differences(postgres_plan, ai_plan)` | both plans | list of difference dictionaries |
| `iter_plan_nodes(plan)` | a `PlanNode` | `(node_id, PlanNode)` pairs, for the GUI |
| `format_comparison(result)` | `comparison_result` | plain-text report |

**Inputs.** `postgres_plan` may be the `PlanNode` from `parse_postgres_plan`
or the raw `EXPLAIN (FORMAT JSON)` output from `get_postgres_plan`. `ai_plan`
may be the `PlanNode` from `get_ai_plan` or raw AI JSON. Raw input is converted
with Member 1's and Member 2's parsers. The input plans are never modified.

**Node IDs.** A node is identified by its path in the `PlanNode` tree: root
`"0"`, its children `"0.0"`, `"0.1"`, grandchild `"0.1.0"`. `iter_plan_nodes`
produces the same IDs, so `display_comparison` can colour the nodes named in
matches and differences.

## Approach

### 1. Normalisation

PostgreSQL and the LLM name things differently, so each node is first mapped
to a shared vocabulary:

| Field | PostgreSQL key | AI key |
| --- | --- | --- |
| Table | `Relation Name` | `relation_name` |
| Alias | `Alias` | `alias` |
| Logical join type | `Join Type` | `join_type` |
| Conditions | `Hash Cond`, `Merge Cond`, `Join Filter`, `Index Cond`, `Filter`, ... | `condition` |
| Index | `Index Name` | `index_name` |
| Sort / group key | `Sort Key`, `Group Key` | `sort_key`, `group_key` |
| Rows / cost | `Plan Rows`, `Total Cost` | `estimated_rows`, `estimated_cost` |

Operator synonyms are unified ("Sequential Scan" becomes `Seq Scan`, "Nested
Loop Join" becomes `Nested Loop`). PostgreSQL's `Aggregate` node with
`Strategy: Hashed`/`Sorted` becomes `HashAggregate`/`GroupAggregate`. A generic
AI `Scan` node with a `scan_type` property is resolved to the specific scan.
Conditions are compared after removing casts, quotes, parentheses and table
qualifiers, so `((c.c_mktsegment)::text = 'BUILDING'::text)` equals
`c_mktsegment = 'building'`.

Each operator is assigned a category: `scan`, `join`, `aggregate`, `sort`,
`auxiliary` (PostgreSQL helper nodes: `Hash`, `Materialize`, `Memoize`,
`Gather`, `Gather Merge`, `Bitmap Index Scan`) or `other` (`Limit`, `Unique`,
...).

### 2. Node matching (`match_nodes`)

Ordered tree edit distance alone is a poor matcher for QEPs: swapping a join's
two inputs, which changes little logically, shows up as two relabels. Matching
is therefore anchored on the base tables underneath each node:

1. For every node, collect the set of tables in its subtree.
2. Two nodes are candidates if they are in the same category and their table
   sets overlap. `other` and `auxiliary` nodes must also have the same
   operator.
3. Candidates are ranked by Jaccard overlap of the table sets (1.0 means same
   tables), then same operator, then same alias (separates self-joins such as
   `nation n1`/`nation n2`), then closest depth.
4. Pairs are taken greedily in that order, each node at most once.

A matched pair with different operators (Seq Scan ↔ Index Scan) is a method
difference. An unmatched node is a missing or additional operation.

### 3. Difference detection (`identify_differences`)

| `type` | Detected when |
| --- | --- |
| `scan_method` | matched scans use different access methods |
| `join_method` | matched joins use different algorithms |
| `aggregate_method` | matched aggregates differ (e.g. HashAggregate vs GroupAggregate) |
| `sort_method` | Sort vs Incremental Sort |
| `join_order` | the join trees group the tables differently |
| `join_input_order` | same grouping, but a join's inputs are on opposite sides |
| `join_type` | matched joins have different logical types (Inner vs Semi, ...) |
| `index_used` | matched scans use different indexes |
| `condition` | matched nodes both have conditions and they differ |
| `sort_key` / `group_key` | matched nodes both have keys and they differ |
| `missing_operation` | a PostgreSQL node has no counterpart in the AI plan |
| `additional_operation` | an AI node has no counterpart in PostgreSQL's plan |
| `auxiliary_operation` | an unmatched helper node (either plan) |

Property differences are reported only when both plans state the property,
because the LLM often leaves fields empty.

Each difference carries a `description` (what differs) and an `explanation`
(what the operators do and why a planner might choose differently).

### 4. Tree edit distance

`compare_plans` also returns a Zhang–Shasha ordered tree edit distance between
the two trees with helper nodes removed. Insert and delete cost 1; relabelling
costs 0 for the same operator on the same table, 0.5 for another operator of
the same kind on the same table, and 1 otherwise. This is a structural measure
Member 4 can use; the difference rate itself is Member 4's
`calculate_difference_rate(comparison_result)`.

## `comparison_result` format

```python
{
    "matches": [
        {
            "postgres": NODE, "ai": NODE,
            "category": "scan" | "join" | "aggregate" | "sort" | "other" | "auxiliary",
            "same_operator": bool,
            "match_basis": "same tables" | "overlapping tables (67%)" | ...,
        },
    ],
    "unmatched_postgres": [NODE, ...],
    "unmatched_ai": [NODE, ...],
    "differences": [
        {
            "type": "scan_method" | ... (table above),
            "category": "scan" | "join" | "aggregate" | "sort" | "other" | "auxiliary",
            "postgres_node_id": "0.1" | None,
            "ai_node_id": "0.0" | None,
            "postgres_value": ..., "ai_value": ...,
            "description": str,
            "explanation": str,
        },
    ],
    "difference_counts": {"scan_method": 1, ...},
    "tree_edit_distance": float,
    "postgres_summary": SUMMARY,
    "ai_summary": SUMMARY,
    "estimates": [
        {"postgres_node_id", "ai_node_id", "label",
         "postgres_rows", "ai_rows", "postgres_cost", "ai_cost"},
    ],
    "identical": bool,          # no differences at all
    "notes": [str, ...],
}

NODE = {"node_id", "operator", "original_type", "category",
        "relation", "relations", "label"}

SUMMARY = {"node_count", "auxiliary_node_count", "depth", "root_operator",
           "operators", "relations", "scan_count", "join_count",
           "join_order", "estimated_rows", "estimated_cost"}
```

Suggested GUI colouring: matched nodes with `same_operator` true in one
colour, matched nodes with a method difference in another, and
`unmatched_postgres` / `unmatched_ai` nodes in a third.

## Limitations

- Matching is greedy, not globally optimal. It works well for typical QEP
  shapes but can pair nodes sub-optimally when many operators share the same
  tables.
- Conditions are compared as normalised text, not semantically, so
  equivalent predicates written in different ways (e.g. `BETWEEN` vs two
  comparisons) are reported as different.
- AI row and cost estimates are not based on PostgreSQL statistics and are
  listed for information only; they are not treated as differences.
- Operator names an LLM invents outside the synonym list are compared as-is
  and will show up as missing/additional operations.
