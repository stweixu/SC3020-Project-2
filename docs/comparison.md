# QEP comparison

Member 3's component lives in `src/comparison.py`.

## Interface

```python
from src.comparison import compare_plans, format_comparison

result = compare_plans(postgres_plan, ai_plan)
print(format_comparison(result))
```

- `postgres_plan`: a `PlanNode` from `parse_postgres_plan`, or the raw
  `EXPLAIN (FORMAT JSON)` output from `get_postgres_plan` (converted
  automatically).
- `ai_plan`: a `PlanNode` from `get_ai_plan`, or raw AI JSON (converted with
  `parse_ai_plan`).
- `ignore_auxiliary` (keyword, default `True`): see below.

## Normalisation

PostgreSQL and the LLM use different property keys and operator names, so both
trees are mapped to a shared vocabulary before comparison:

| Field | PostgreSQL key | AI key |
| --- | --- | --- |
| Relation | `Relation Name` | `relation_name` |
| Logical join type | `Join Type` | `join_type` |
| Condition | `Hash Cond`, `Index Cond`, `Filter`, ... | `condition` |
| Rows | `Plan Rows` | `estimated_rows` |
| Cost | `Total Cost` | `estimated_cost` |

Operator synonyms are unified ("Sequential Scan" becomes `Seq Scan`, "Nested
Loop Join" becomes `Nested Loop`). PostgreSQL's `Aggregate` node with
`Strategy: Hashed`/`Sorted` becomes `HashAggregate`/`GroupAggregate` to match
the names an LLM typically uses. Generic AI nodes such as `Scan` with a
`scan_type` property are resolved to the specific scan.

PostgreSQL adds helper nodes (`Hash`, `Materialize`, `Memoize`, `Gather`,
`Gather Merge`) that an LLM usually leaves out. By default they are removed
before the structural comparison (their children take their place) and any
difference in them is reported in the `auxiliary` category.

## Difference rate

`difference_rate = tree_edit_distance / max(node_count_pg, node_count_ai)`,
capped at 1.0. The tree edit distance is the ordered Zhang–Shasha algorithm
with:

- insert / delete: 1
- relabel to the same operator on the same relation: 0
- relabel to another operator of the same kind (Seq Scan vs Index Scan on one
  table, Hash Join vs Merge Join, HashAggregate vs GroupAggregate): 0.5
- any other relabel, including a scan of a different table: 1

The distance is ordered, so swapping a join's inputs (a different hash build or
outer side) counts as a difference. The `join_order` entry in `differences`
states whether the join grouping itself differs or only the input sides do.

## Result

```text
difference_rate      float, 0.0 identical .. 1.0 completely different
similarity           1 - difference_rate
tree_edit_distance   float
identical_structure  bool
postgres_summary     node_count, depth, operators, relations, scan_count,
ai_summary           join_count, join_order, root_operator,
                     estimated_rows, estimated_cost
differences          list of {category, description, postgres, ai}
estimates            list of {item, postgres, ai, ai_to_postgres_ratio}
notes                list of caveats to display with the result
```

`differences` categories are `root`, `scan`, `join`, `join_order`,
`aggregate`, `sort`, `other`, and `auxiliary`. Joins are matched between the
plans by the set of relations underneath them, so a join of `{customer,
orders}` is compared with the same join in the other plan wherever it sits.

## Limitations

- Conditions are normalised (`normalize_condition`) but not used in the
  difference rate, because LLM predicate text varies too much to score
  reliably.
- AI row and cost estimates are not based on PostgreSQL statistics. They are
  shown side by side for information only.
- Self-joins of the same table are matched by relation name, so two scans of
  one table are compared as a group.
