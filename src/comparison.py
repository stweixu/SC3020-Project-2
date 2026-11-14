"""Compare PostgreSQL and AI-generated QEP trees.

Member 3's component. ``compare_plans`` takes the PostgreSQL plan and the AI
plan, normalises both into a common vocabulary, and returns a dictionary that
the interface (Member 4) can display:

- a structural difference rate based on tree edit distance,
- per-plan summaries (node counts, depth, operators, relations),
- a list of human-readable differences (scan methods, join algorithms, join
  order, aggregation/sorting operators, extra or missing operators),
- a side-by-side comparison of row and cost estimates.

PostgreSQL and the LLM name things differently (``Relation Name`` vs
``relation_name``, ``Aggregate`` + ``Strategy: Hashed`` vs ``HashAggregate``),
so every node is first mapped to a ``NormalizedNode``.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from src.plan_node import PlanNode


# ---------------------------------------------------------------------------
# Operator vocabulary
# ---------------------------------------------------------------------------

# Lower-cased, space-collapsed operator names -> canonical PostgreSQL names.
_OPERATOR_SYNONYMS = {
    # Scans
    "seq scan": "Seq Scan",
    "sequential scan": "Seq Scan",
    "full scan": "Seq Scan",
    "full table scan": "Seq Scan",
    "table scan": "Seq Scan",
    "parallel seq scan": "Seq Scan",
    "index scan": "Index Scan",
    "index only scan": "Index Only Scan",
    "index-only scan": "Index Only Scan",
    "bitmap heap scan": "Bitmap Heap Scan",
    "bitmap index scan": "Bitmap Index Scan",
    "bitmap scan": "Bitmap Heap Scan",
    "tid scan": "Tid Scan",
    "subquery scan": "Subquery Scan",
    "cte scan": "CTE Scan",
    "function scan": "Function Scan",
    "values scan": "Values Scan",
    # Joins
    "hash join": "Hash Join",
    "nested loop": "Nested Loop",
    "nested loop join": "Nested Loop",
    "nested loops": "Nested Loop",
    "nl join": "Nested Loop",
    "merge join": "Merge Join",
    "sort merge join": "Merge Join",
    "sort-merge join": "Merge Join",
    # Aggregation
    "aggregate": "Aggregate",
    "plain aggregate": "Aggregate",
    "hashaggregate": "HashAggregate",
    "hash aggregate": "HashAggregate",
    "hash aggregation": "HashAggregate",
    "groupaggregate": "GroupAggregate",
    "group aggregate": "GroupAggregate",
    "sorted aggregate": "GroupAggregate",
    "mixedaggregate": "MixedAggregate",
    "mixed aggregate": "MixedAggregate",
    "group": "Group",
    "windowagg": "WindowAgg",
    "window aggregate": "WindowAgg",
    # Sorting / limiting
    "sort": "Sort",
    "incremental sort": "Incremental Sort",
    "top-n sort": "Sort",
    "limit": "Limit",
    "unique": "Unique",
    # Auxiliary / execution helpers
    "hash": "Hash",
    "materialize": "Materialize",
    "memoize": "Memoize",
    "gather": "Gather",
    "gather merge": "Gather Merge",
    "result": "Result",
    "append": "Append",
    "merge append": "Merge Append",
    "setop": "SetOp",
    "hashsetop": "SetOp",
}

# PostgreSQL reports all aggregation as "Aggregate" plus a "Strategy" property.
_AGGREGATE_STRATEGIES = {
    "plain": "Aggregate",
    "hashed": "HashAggregate",
    "sorted": "GroupAggregate",
    "mixed": "MixedAggregate",
}

_SCAN_OPERATORS = {
    "Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan",
    "Bitmap Index Scan", "Tid Scan", "Subquery Scan", "CTE Scan",
    "Function Scan", "Values Scan",
}
_JOIN_OPERATORS = {"Hash Join", "Nested Loop", "Merge Join"}
_AGGREGATE_OPERATORS = {
    "Aggregate", "HashAggregate", "GroupAggregate", "MixedAggregate",
    "Group", "WindowAgg",
}
_SORT_OPERATORS = {"Sort", "Incremental Sort"}

# Nodes PostgreSQL adds as execution helpers. An LLM usually omits them, so by
# default they are removed before the structural comparison (their children
# take their place) and reported separately.
AUXILIARY_OPERATORS = frozenset(
    {"Hash", "Materialize", "Memoize", "Gather", "Gather Merge"}
)

# Property keys for each normalised field, PostgreSQL first, then AI.
_RELATION_KEYS = ("Relation Name", "relation_name", "relation", "table")
_ALIAS_KEYS = ("Alias", "alias")
_INDEX_KEYS = ("Index Name", "index_name", "index")
_JOIN_TYPE_KEYS = ("Join Type", "join_type")
_SCAN_TYPE_KEYS = ("scan_type",)
_STRATEGY_KEYS = ("Strategy", "strategy")
_ROWS_KEYS = ("Plan Rows", "estimated_rows", "rows")
_COST_KEYS = ("Total Cost", "estimated_cost", "cost")
_CONDITION_KEYS = (
    "Hash Cond", "Merge Cond", "Join Filter", "Index Cond", "Recheck Cond",
    "Filter", "condition", "filter",
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@dataclass
class NormalizedNode:
    """A plan node expressed in a vocabulary shared by both plan sources."""

    operator: str
    category: str
    relation: str = None
    alias: str = None
    index: str = None
    join_type: str = None
    conditions: tuple = ()
    rows: float = None
    cost: float = None
    children: list = field(default_factory=list)
    original_type: str = None

    def label(self):
        """Short text label, e.g. ``Seq Scan on orders``."""
        if self.relation:
            return f"{self.operator} on {self.relation}"
        return self.operator


def _first(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", "null"):
            return value
    return None


def _to_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _clean_name(value):
    if value is None:
        return None
    text = str(value).strip().strip('"').lower()
    return text or None


def _canonical_operator(node_type, properties):
    key = re.sub(r"\s+", " ", node_type.strip().lower())
    operator = _OPERATOR_SYNONYMS.get(key, node_type.strip())

    if operator == "Aggregate":
        strategy = _first(properties, _STRATEGY_KEYS)
        if strategy:
            operator = _AGGREGATE_STRATEGIES.get(str(strategy).lower(), operator)

    # An LLM may describe a node generically ("Scan", "Join") and put the
    # algorithm in a property instead.
    if key in ("scan", "table access"):
        scan_type = _first(properties, _SCAN_TYPE_KEYS)
        if scan_type:
            operator = _canonical_operator(str(scan_type), {})
    elif key == "join":
        join_type = _first(properties, _JOIN_TYPE_KEYS)
        if join_type:
            candidate = _canonical_operator(str(join_type), {})
            if candidate in _JOIN_OPERATORS:
                operator = candidate
    return operator


def _category(operator):
    if operator in _SCAN_OPERATORS:
        return "scan"
    if operator in _JOIN_OPERATORS:
        return "join"
    if operator in _AGGREGATE_OPERATORS:
        return "aggregate"
    if operator in _SORT_OPERATORS:
        return "sort"
    if operator in AUXILIARY_OPERATORS:
        return "auxiliary"
    return "other"


def _logical_join_type(value):
    """Return Inner/Left/Semi/... or None if the value names an algorithm."""
    if value is None:
        return None
    text = str(value).strip()
    if _OPERATOR_SYNONYMS.get(text.lower()) in _JOIN_OPERATORS:
        return None
    text = re.sub(r"\s*join$", "", text, flags=re.IGNORECASE)
    return text.capitalize() or None


def normalize_condition(condition):
    """Normalise a predicate so equivalent PostgreSQL/AI text compares equal.

    Removes type casts, quotes, parentheses, table/alias qualifiers and
    whitespace differences, then sorts the sides of a simple equality.
    """
    if condition is None:
        return None
    text = str(condition).lower()
    text = re.sub(r"::[a-z_ ]+(\([0-9, ]*\))?(\[\])?", "", text)
    text = text.replace('"', "")
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"\b[a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)\b", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"\s+and\s+", text)
    normalised_parts = []
    for part in parts:
        sides = [side.strip() for side in part.split(" = ")]
        if len(sides) == 2:
            part = " = ".join(sorted(sides))
        normalised_parts.append(part)
    return " and ".join(sorted(normalised_parts)) or None


def _coerce_plan(plan, source):
    """Accept a PlanNode, or raw PostgreSQL / AI JSON, and return a PlanNode."""
    if isinstance(plan, PlanNode):
        return plan
    if plan is None:
        raise ValueError(f"{source} plan is missing.")
    if source == "PostgreSQL":
        # Lazy import: preprocessing pulls in psycopg and the Gemini SDK.
        from src.preprocessing import parse_postgres_plan

        return parse_postgres_plan(plan)
    from src.preprocessing import parse_ai_plan

    return parse_ai_plan(plan)


def normalize_plan(node, ignore_auxiliary=True):
    """Convert a ``PlanNode`` tree into a list of ``NormalizedNode`` roots.

    A list is returned because removing an auxiliary root (e.g. ``Gather``)
    could in principle expose several children. Normally it has one item.
    """
    properties = node.properties or {}
    operator = _canonical_operator(node.node_type, properties)
    children = []
    for child in node.children or []:
        children.extend(normalize_plan(child, ignore_auxiliary))

    category = _category(operator)
    if ignore_auxiliary and category == "auxiliary":
        return children

    conditions = []
    for key in _CONDITION_KEYS:
        value = properties.get(key)
        if value not in (None, "", "null"):
            conditions.append(normalize_condition(value))

    return [
        NormalizedNode(
            operator=operator,
            category=category,
            relation=_clean_name(_first(properties, _RELATION_KEYS)),
            alias=_clean_name(_first(properties, _ALIAS_KEYS)),
            index=_clean_name(_first(properties, _INDEX_KEYS)),
            join_type=_logical_join_type(_first(properties, _JOIN_TYPE_KEYS)),
            conditions=tuple(sorted(c for c in conditions if c)),
            rows=_to_number(_first(properties, _ROWS_KEYS)),
            cost=_to_number(_first(properties, _COST_KEYS)),
            children=children,
            original_type=node.node_type,
        )
    ]


def _single_root(nodes, source):
    if not nodes:
        raise ValueError(f"{source} plan has no comparable operators.")
    if len(nodes) == 1:
        return nodes[0]
    return NormalizedNode(operator="Result", category="other", children=nodes)


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _iter_nodes(node):
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _depth(node):
    return 1 + max((_depth(child) for child in node.children), default=0)


def _relations_under(node):
    return frozenset(n.relation for n in _iter_nodes(node) if n.relation)


def _join_tree(node):
    """Join order as nested tuples of relation names, e.g. ((a, b), c)."""
    if node.category == "join":
        parts = [_join_tree(child) for child in node.children]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else tuple(parts)
    if node.relation and node.category == "scan":
        return node.relation
    parts = [p for p in (_join_tree(c) for c in node.children) if p is not None]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else tuple(parts)


def _unordered(tree):
    """Join tree with build/probe sides ignored, for order-insensitive equality."""
    if isinstance(tree, tuple):
        return frozenset(_unordered(part) for part in tree)
    return tree


def format_join_tree(tree):
    """Render a join tree such as ``((customer ⋈ orders) ⋈ lineitem)``."""
    if tree is None:
        return "(no joins)"
    if isinstance(tree, tuple):
        return "(" + " ⋈ ".join(format_join_tree(part) for part in tree) + ")"
    return str(tree)


def summarize_plan(root):
    """Return counts and lists describing one normalised plan."""
    nodes = list(_iter_nodes(root))
    scans = [n for n in nodes if n.category == "scan"]
    joins = [n for n in nodes if n.category == "join"]
    return {
        "node_count": len(nodes),
        "depth": _depth(root),
        "operators": dict(Counter(n.operator for n in nodes)),
        "relations": sorted({n.relation for n in nodes if n.relation}),
        "scan_count": len(scans),
        "join_count": len(joins),
        "join_order": format_join_tree(_join_tree(root)),
        "root_operator": root.operator,
        "estimated_rows": root.rows,
        "estimated_cost": root.cost,
    }


# ---------------------------------------------------------------------------
# Tree edit distance (Zhang–Shasha)
# ---------------------------------------------------------------------------


def _rename_cost(a, b):
    """Relabel cost.

    0 for the same operator on the same relation, 0.5 for a different operator
    of the same kind (e.g. Seq Scan vs Index Scan on one table, Hash Join vs
    Merge Join), and 1 otherwise, including scans of different tables.
    """
    if a.relation != b.relation and (a.relation or b.relation):
        return 1.0
    if a.operator == b.operator:
        return 0.0
    if a.category == b.category:
        return 0.5
    return 1.0


def tree_edit_distance(root_a, root_b):
    """Ordered tree edit distance with unit insert/delete and graded relabel."""

    def prepare(root):
        postorder, leftmost = [], []

        def walk(node):
            first_leaf = None
            for child in node.children:
                child_leaf = walk(child)
                if first_leaf is None:
                    first_leaf = child_leaf
            index = len(postorder)
            postorder.append(node)
            leftmost.append(index if first_leaf is None else first_leaf)
            return leftmost[index]

        walk(root)
        keyroots = []
        seen = set()
        for i in range(len(postorder) - 1, -1, -1):
            if leftmost[i] not in seen:
                seen.add(leftmost[i])
                keyroots.append(i)
        return postorder, leftmost, sorted(keyroots)

    nodes_a, left_a, keys_a = prepare(root_a)
    nodes_b, left_b, keys_b = prepare(root_b)
    distance = [[0.0] * len(nodes_b) for _ in nodes_a]

    for i in keys_a:
        for j in keys_b:
            li, lj = left_a[i], left_b[j]
            rows, cols = i - li + 2, j - lj + 2
            forest = [[0.0] * cols for _ in range(rows)]
            for x in range(1, rows):
                forest[x][0] = forest[x - 1][0] + 1
            for y in range(1, cols):
                forest[0][y] = forest[0][y - 1] + 1
            for x in range(1, rows):
                for y in range(1, cols):
                    a_index, b_index = li + x - 1, lj + y - 1
                    if left_a[a_index] == li and left_b[b_index] == lj:
                        forest[x][y] = min(
                            forest[x - 1][y] + 1,
                            forest[x][y - 1] + 1,
                            forest[x - 1][y - 1]
                            + _rename_cost(nodes_a[a_index], nodes_b[b_index]),
                        )
                        distance[a_index][b_index] = forest[x][y]
                    else:
                        px = left_a[a_index] - li
                        py = left_b[b_index] - lj
                        forest[x][y] = min(
                            forest[x - 1][y] + 1,
                            forest[x][y - 1] + 1,
                            forest[px][py] + distance[a_index][b_index],
                        )
    return distance[-1][-1]


# ---------------------------------------------------------------------------
# Detailed differences
# ---------------------------------------------------------------------------


def _difference(category, description, postgres=None, ai=None):
    return {
        "category": category,
        "description": description,
        "postgres": postgres,
        "ai": ai,
    }


def _compare_scans(pg_root, ai_root):
    differences = []
    pg_scans = {}
    ai_scans = {}
    for node in _iter_nodes(pg_root):
        if node.category == "scan" and node.relation:
            pg_scans.setdefault(node.relation, []).append(node)
    for node in _iter_nodes(ai_root):
        if node.category == "scan" and node.relation:
            ai_scans.setdefault(node.relation, []).append(node)

    for relation in sorted(set(pg_scans) | set(ai_scans)):
        pg_ops = sorted(n.operator for n in pg_scans.get(relation, []))
        ai_ops = sorted(n.operator for n in ai_scans.get(relation, []))
        if not ai_ops:
            differences.append(_difference(
                "scan",
                f"PostgreSQL scans '{relation}' ({', '.join(pg_ops)}) but the "
                "AI plan does not access it.",
                pg_ops, None,
            ))
        elif not pg_ops:
            differences.append(_difference(
                "scan",
                f"The AI plan scans '{relation}' ({', '.join(ai_ops)}) but "
                "PostgreSQL does not access it.",
                None, ai_ops,
            ))
        elif pg_ops != ai_ops:
            differences.append(_difference(
                "scan",
                f"Different access method for '{relation}': PostgreSQL uses "
                f"{', '.join(pg_ops)}, the AI plan uses {', '.join(ai_ops)}.",
                pg_ops, ai_ops,
            ))
        else:
            pg_index = sorted(filter(None, (n.index for n in pg_scans[relation])))
            ai_index = sorted(filter(None, (n.index for n in ai_scans[relation])))
            if pg_index and ai_index and pg_index != ai_index:
                differences.append(_difference(
                    "scan",
                    f"Both plans use {pg_ops[0]} on '{relation}' but with "
                    f"different indexes ({', '.join(pg_index)} vs "
                    f"{', '.join(ai_index)}).",
                    pg_index, ai_index,
                ))
    return differences


def _compare_joins(pg_root, ai_root):
    differences = []
    pg_joins = {
        _relations_under(n): n
        for n in _iter_nodes(pg_root)
        if n.category == "join"
    }
    ai_joins = {
        _relations_under(n): n
        for n in _iter_nodes(ai_root)
        if n.category == "join"
    }

    for relations in sorted(set(pg_joins) & set(ai_joins), key=sorted):
        pg_join, ai_join = pg_joins[relations], ai_joins[relations]
        names = ", ".join(sorted(relations)) or "(no relations)"
        if pg_join.operator != ai_join.operator:
            differences.append(_difference(
                "join",
                f"Join of {{{names}}}: PostgreSQL uses {pg_join.operator}, "
                f"the AI plan uses {ai_join.operator}.",
                pg_join.operator, ai_join.operator,
            ))
        if (
            pg_join.join_type
            and ai_join.join_type
            and pg_join.join_type != ai_join.join_type
        ):
            differences.append(_difference(
                "join",
                f"Join of {{{names}}}: logical join type differs "
                f"({pg_join.join_type} vs {ai_join.join_type}).",
                pg_join.join_type, ai_join.join_type,
            ))

    pg_tree, ai_tree = _join_tree(pg_root), _join_tree(ai_root)
    if pg_joins or ai_joins:
        if _unordered(pg_tree) != _unordered(ai_tree):
            differences.append(_difference(
                "join_order",
                "Different join order: PostgreSQL joins "
                f"{format_join_tree(pg_tree)}, the AI plan joins "
                f"{format_join_tree(ai_tree)}.",
                format_join_tree(pg_tree), format_join_tree(ai_tree),
            ))
        elif pg_tree != ai_tree:
            differences.append(_difference(
                "join_order",
                "Same join grouping, but the inputs of at least one join are "
                "swapped (e.g. a different hash build or outer side).",
                format_join_tree(pg_tree), format_join_tree(ai_tree),
            ))
    return differences


def _compare_operator_counts(pg_summary, ai_summary):
    differences = []
    pg_ops = Counter(pg_summary["operators"])
    ai_ops = Counter(ai_summary["operators"])
    for category, operators in (
        ("aggregate", _AGGREGATE_OPERATORS),
        ("sort", _SORT_OPERATORS),
        ("other", None),
    ):
        relevant = sorted(
            op for op in set(pg_ops) | set(ai_ops)
            if (op in operators if operators else _category(op) == "other")
        )
        for operator in relevant:
            if pg_ops[operator] != ai_ops[operator]:
                differences.append(_difference(
                    category,
                    f"{operator}: {pg_ops[operator]} in PostgreSQL vs "
                    f"{ai_ops[operator]} in the AI plan.",
                    pg_ops[operator], ai_ops[operator],
                ))
    return differences


def _compare_auxiliary(pg_full, ai_full):
    pg_aux = Counter(
        n.operator for n in _iter_nodes(pg_full) if n.category == "auxiliary"
    )
    ai_aux = Counter(
        n.operator for n in _iter_nodes(ai_full) if n.category == "auxiliary"
    )
    differences = []
    for operator in sorted(set(pg_aux) | set(ai_aux)):
        if pg_aux[operator] != ai_aux[operator]:
            differences.append(_difference(
                "auxiliary",
                f"{operator} (helper node, excluded from the difference rate): "
                f"{pg_aux[operator]} in PostgreSQL vs {ai_aux[operator]} in the "
                "AI plan.",
                pg_aux[operator], ai_aux[operator],
            ))
    return differences


def _estimate_row(label, pg_value, ai_value):
    ratio = None
    if pg_value and ai_value is not None:
        ratio = round(ai_value / pg_value, 4)
    return {"item": label, "postgres": pg_value, "ai": ai_value, "ai_to_postgres_ratio": ratio}


def _compare_estimates(pg_root, ai_root):
    rows = [
        _estimate_row("Root estimated rows", pg_root.rows, ai_root.rows),
        _estimate_row("Root estimated total cost", pg_root.cost, ai_root.cost),
    ]
    pg_scans = {
        n.relation: n for n in _iter_nodes(pg_root)
        if n.category == "scan" and n.relation
    }
    ai_scans = {
        n.relation: n for n in _iter_nodes(ai_root)
        if n.category == "scan" and n.relation
    }
    for relation in sorted(set(pg_scans) & set(ai_scans)):
        rows.append(_estimate_row(
            f"Rows from scan of {relation}",
            pg_scans[relation].rows,
            ai_scans[relation].rows,
        ))
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_plans(postgres_plan, ai_plan, *, ignore_auxiliary=True):
    """Compare a PostgreSQL QEP with an AI-generated QEP.

    Args:
        postgres_plan: ``PlanNode`` root, or the raw ``EXPLAIN (FORMAT JSON)``
            output returned by ``get_postgres_plan``.
        ai_plan: ``PlanNode`` root, or raw AI JSON accepted by
            ``parse_ai_plan``.
        ignore_auxiliary: If true, PostgreSQL helper nodes (Hash, Materialize,
            Memoize, Gather, Gather Merge) are excluded from the structural
            score and reported separately.

    Returns:
        A dictionary with keys:

        - ``difference_rate``: 0.0 (identical) to 1.0 (completely different),
          the tree edit distance divided by the larger plan's node count.
        - ``similarity``: ``1 - difference_rate``.
        - ``tree_edit_distance``: raw edit distance.
        - ``identical_structure``: true when the edit distance is zero.
        - ``postgres_summary`` / ``ai_summary``: see ``summarize_plan``.
        - ``differences``: list of ``{category, description, postgres, ai}``.
        - ``estimates``: row/cost estimates side by side.
        - ``notes``: caveats to show alongside the result.
    """
    pg_node = _coerce_plan(postgres_plan, "PostgreSQL")
    ai_node = _coerce_plan(ai_plan, "AI")

    pg_root = _single_root(normalize_plan(pg_node, ignore_auxiliary), "PostgreSQL")
    ai_root = _single_root(normalize_plan(ai_node, ignore_auxiliary), "AI")

    pg_summary = summarize_plan(pg_root)
    ai_summary = summarize_plan(ai_root)

    distance = tree_edit_distance(pg_root, ai_root)
    largest = max(pg_summary["node_count"], ai_summary["node_count"])
    difference_rate = min(1.0, distance / largest) if largest else 0.0

    differences = []
    if pg_root.operator != ai_root.operator:
        differences.append(_difference(
            "root",
            f"Root operator differs: PostgreSQL {pg_root.operator}, AI "
            f"plan {ai_root.operator}.",
            pg_root.operator, ai_root.operator,
        ))
    differences.extend(_compare_scans(pg_root, ai_root))
    differences.extend(_compare_joins(pg_root, ai_root))
    differences.extend(_compare_operator_counts(pg_summary, ai_summary))
    if ignore_auxiliary:
        pg_full = _single_root(normalize_plan(pg_node, False), "PostgreSQL")
        ai_full = _single_root(normalize_plan(ai_node, False), "AI")
        differences.extend(_compare_auxiliary(pg_full, ai_full))

    notes = [
        "The AI plan is a proposed plan; it is not executed and its row and "
        "cost estimates are not based on PostgreSQL statistics.",
    ]
    if ignore_auxiliary:
        notes.append(
            "Helper nodes (Hash, Materialize, Memoize, Gather, Gather Merge) "
            "are excluded from the difference rate."
        )

    return {
        "difference_rate": round(difference_rate, 4),
        "similarity": round(1.0 - difference_rate, 4),
        "tree_edit_distance": distance,
        "identical_structure": distance == 0,
        "postgres_summary": pg_summary,
        "ai_summary": ai_summary,
        "differences": differences,
        "estimates": _compare_estimates(pg_root, ai_root),
        "notes": notes,
    }


def format_comparison(result):
    """Render a ``compare_plans`` result as plain text (CLI / debugging)."""
    pg, ai = result["postgres_summary"], result["ai_summary"]
    lines = [
        f"Difference rate: {result['difference_rate']:.1%} "
        f"(similarity {result['similarity']:.1%}, "
        f"tree edit distance {result['tree_edit_distance']:g})",
        "",
        f"{'':<16}{'PostgreSQL':<36}AI",
        f"{'Nodes':<16}{pg['node_count']:<36}{ai['node_count']}",
        f"{'Depth':<16}{pg['depth']:<36}{ai['depth']}",
        f"{'Root':<16}{pg['root_operator']:<36}{ai['root_operator']}",
        f"{'Joins':<16}{pg['join_count']:<36}{ai['join_count']}",
        f"{'Join order':<16}{pg['join_order']:<36}{ai['join_order']}",
        "",
        "Differences:",
    ]
    if result["differences"]:
        lines.extend(f"  - [{d['category']}] {d['description']}" for d in result["differences"])
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Estimates:")
    for row in result["estimates"]:
        lines.append(f"  - {row['item']}: PostgreSQL {row['postgres']}, AI {row['ai']}")
    lines.append("")
    lines.extend(f"Note: {note}" for note in result["notes"])
    return "\n".join(lines)
