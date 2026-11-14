"""Compare PostgreSQL and AI-generated QEP trees (Member 3).

Public functions
----------------
``match_nodes(postgres_plan, ai_plan)``
    Pair up corresponding operators in the two plans.
``identify_differences(postgres_plan, ai_plan)``
    List scan-method, join-method, join-order, missing/additional operation
    and other property differences, each with an explanation.
``compare_plans(postgres_plan, ai_plan)``
    Run both of the above and return the structured ``comparison_result``
    consumed by Member 4's ``calculate_difference_rate`` and
    ``display_comparison``.

All three accept either ``PlanNode`` trees or the raw plan JSON produced by
``get_postgres_plan`` / ``get_ai_plan``. The difference *rate* is deliberately
not computed here: that is Member 4's component. ``comparison_result`` exposes
the counts it needs (matches, unmatched nodes, typed differences and a tree
edit distance).

Node IDs
--------
Every node is identified by its path in the ``PlanNode`` tree: the root is
``"0"``, its children ``"0.0"``, ``"0.1"``, a grandchild ``"0.1.0"`` and so
on. ``iter_plan_nodes`` yields the same IDs, so the GUI can colour the exact
nodes referred to in matches and differences.

See ``docs/comparison.md`` for the full result format.
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
    # Helpers
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
    "Tid Scan", "Subquery Scan", "CTE Scan", "Function Scan", "Values Scan",
}
_JOIN_OPERATORS = {"Hash Join", "Nested Loop", "Merge Join"}
_AGGREGATE_OPERATORS = {
    "Aggregate", "HashAggregate", "GroupAggregate", "MixedAggregate",
    "Group", "WindowAgg",
}
_SORT_OPERATORS = {"Sort", "Incremental Sort"}

# Helper nodes PostgreSQL inserts as part of executing another operator. An
# LLM usually leaves them out, so they are reported as their own difference
# type ("auxiliary_operation") and excluded from the tree edit distance.
AUXILIARY_OPERATORS = frozenset(
    {"Hash", "Materialize", "Memoize", "Gather", "Gather Merge",
     "Bitmap Index Scan"}
)

# Property keys for each field, PostgreSQL first, then AI.
_RELATION_KEYS = ("Relation Name", "relation_name", "relation", "table")
_ALIAS_KEYS = ("Alias", "alias")
_INDEX_KEYS = ("Index Name", "index_name", "index")
_JOIN_TYPE_KEYS = ("Join Type", "join_type")
_SCAN_TYPE_KEYS = ("scan_type",)
_STRATEGY_KEYS = ("Strategy", "strategy")
_ROWS_KEYS = ("Plan Rows", "estimated_rows", "rows")
_COST_KEYS = ("Total Cost", "estimated_cost", "cost")
_SORT_KEY_KEYS = ("Sort Key", "sort_key", "sort_keys")
_GROUP_KEY_KEYS = ("Group Key", "group_key", "group_keys", "group_by")
_CONDITION_KEYS = (
    "Hash Cond", "Merge Cond", "Join Filter", "Index Cond", "Recheck Cond",
    "Filter", "condition", "filter",
)


# ---------------------------------------------------------------------------
# Explanation text
# ---------------------------------------------------------------------------

_OPERATOR_DESCRIPTIONS = {
    "Seq Scan": "reads every row of the table",
    "Index Scan": "looks up matching rows through an index, then fetches them "
                  "from the table",
    "Index Only Scan": "answers the query from the index alone without "
                       "visiting the table",
    "Bitmap Heap Scan": "collects matching row locations from an index into "
                        "a bitmap, then reads those table pages in order",
    "Hash Join": "builds a hash table on one input and probes it with the "
                 "other",
    "Nested Loop": "scans the inner input once for every row of the outer "
                   "input",
    "Merge Join": "merges two inputs that are both sorted on the join key",
    "Aggregate": "computes aggregates over all input rows as one group",
    "HashAggregate": "groups rows using an in-memory hash table",
    "GroupAggregate": "groups rows that arrive already sorted on the group "
                      "key",
    "MixedAggregate": "combines hashed and sorted grouping (e.g. for "
                      "GROUPING SETS)",
    "Sort": "sorts its input",
    "Incremental Sort": "sorts input that is already partly sorted",
    "Limit": "stops after the requested number of rows",
    "Unique": "removes adjacent duplicate rows",
    "Hash": "builds the hash table used by its parent Hash Join",
    "Materialize": "caches its input in memory so it can be re-read",
    "Memoize": "caches inner lookups of a Nested Loop by parameter value",
    "Gather": "collects rows from parallel worker processes",
    "Gather Merge": "collects sorted rows from parallel workers, keeping "
                    "them in order",
    "Bitmap Index Scan": "scans an index to build the bitmap for its parent "
                         "Bitmap Heap Scan",
}

_TYPE_EXPLANATIONS = {
    "scan_method": (
        "The access method depends on how selective the predicate is, which "
        "indexes exist and how large the table is. PostgreSQL decides using "
        "statistics gathered by ANALYZE; the LLM has no statistics and has to "
        "guess the selectivity."
    ),
    "join_method": (
        "The join algorithm depends on the estimated input sizes, whether the "
        "inputs are already sorted on the join key and whether an index exists "
        "on the inner side. Different cardinality guesses lead to different "
        "choices."
    ),
    "aggregate_method": (
        "Hashed aggregation is usually chosen when the groups fit in memory; "
        "sorted aggregation when the input is already sorted on the group key "
        "or there are too many groups to hash."
    ),
    "sort_method": (
        "An incremental sort can be used when the input is already sorted on a "
        "prefix of the sort key."
    ),
    "join_order": (
        "PostgreSQL searches join orders by estimating the size of each "
        "intermediate result. A different order normally means the LLM "
        "estimated those sizes differently."
    ),
    "join_input_order": (
        "The same tables are joined, but the inputs are on different sides. "
        "For a Hash Join this changes which table the hash table is built on; "
        "for a Nested Loop it changes which table is the outer loop."
    ),
    "join_type": (
        "A different logical join type (e.g. Semi vs Inner) suggests the "
        "query, often an EXISTS/IN subquery, was interpreted differently. This "
        "can change the query result, not just its performance."
    ),
    "index_used": (
        "Both plans read the table through an index but chose different "
        "indexes, so they assume different predicates are the most selective."
    ),
    "missing_operation": (
        "PostgreSQL's plan contains a step the AI plan leaves out."
    ),
    "additional_operation": (
        "The AI plan contains a step PostgreSQL's plan does not need."
    ),
    "auxiliary_operation": (
        "This is an execution helper node rather than a separate logical "
        "step, so the difference is usually one of presentation."
    ),
    "condition": (
        "The predicate is placed or written differently. PostgreSQL may push "
        "a filter into an index condition, or the AI may attach it to a "
        "different node."
    ),
    "sort_key": "The two plans sort on different expressions or in a "
                "different order.",
    "group_key": "The two plans group on different columns.",
}


def _describe(operator):
    description = _OPERATOR_DESCRIPTIONS.get(operator)
    return f"{operator} ({description})" if description else operator


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class NormalizedNode:
    """A plan node expressed in a vocabulary shared by both plan sources."""

    node_id: str
    operator: str
    category: str
    original_type: str
    depth: int
    relation: str = None
    alias: str = None
    index: str = None
    join_type: str = None
    conditions: tuple = ()
    sort_key: tuple = ()
    group_key: tuple = ()
    rows: float = None
    cost: float = None
    children: list = field(default_factory=list)
    relations: frozenset = frozenset()
    plan_node: PlanNode = None

    def label(self):
        """Short text label, e.g. ``Seq Scan on orders``."""
        if self.relation:
            return f"{self.operator} on {self.relation}"
        return self.operator

    def to_dict(self):
        return {
            "node_id": self.node_id,
            "operator": self.operator,
            "original_type": self.original_type,
            "category": self.category,
            "relation": self.relation,
            "relations": sorted(self.relations),
            "label": self.label(),
        }


def _first(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", "null", []):
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
    if operator in AUXILIARY_OPERATORS:
        return "auxiliary"
    if operator in _SCAN_OPERATORS:
        return "scan"
    if operator in _JOIN_OPERATORS:
        return "join"
    if operator in _AGGREGATE_OPERATORS:
        return "aggregate"
    if operator in _SORT_OPERATORS:
        return "sort"
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
    whitespace differences, lower-cases everything and sorts the sides of
    simple equalities and the terms of an AND.
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


def _key_list(value):
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.split(",")
    return tuple(k for k in (normalize_condition(v) for v in value) if k)


def _coerce_plan(plan, source):
    """Accept a PlanNode, or raw PostgreSQL / AI JSON, and return a PlanNode."""
    if isinstance(plan, PlanNode):
        return plan
    if plan is None:
        raise ValueError(f"{source} plan is missing.")
    # Lazy import: preprocessing pulls in psycopg and the Gemini SDK, which
    # are not needed when PlanNode trees are passed in.
    if source == "PostgreSQL":
        from src.preprocessing import parse_postgres_plan

        return parse_postgres_plan(plan)
    from src.preprocessing import parse_ai_plan

    return parse_ai_plan(plan)


def _normalize(node, node_id="0", depth=0):
    properties = node.properties or {}
    operator = _canonical_operator(node.node_type, properties)
    children = [
        _normalize(child, f"{node_id}.{i}", depth + 1)
        for i, child in enumerate(node.children or [])
    ]
    conditions = sorted(
        c for c in (
            normalize_condition(properties.get(key)) for key in _CONDITION_KEYS
            if properties.get(key) not in (None, "", "null")
        ) if c
    )
    normalized = NormalizedNode(
        node_id=node_id,
        operator=operator,
        category=_category(operator),
        original_type=node.node_type,
        depth=depth,
        relation=_clean_name(_first(properties, _RELATION_KEYS)),
        alias=_clean_name(_first(properties, _ALIAS_KEYS)),
        index=_clean_name(_first(properties, _INDEX_KEYS)),
        join_type=_logical_join_type(_first(properties, _JOIN_TYPE_KEYS)),
        conditions=tuple(conditions),
        sort_key=_key_list(_first(properties, _SORT_KEY_KEYS)),
        group_key=tuple(sorted(_key_list(_first(properties, _GROUP_KEY_KEYS)))),
        rows=_to_number(_first(properties, _ROWS_KEYS)),
        cost=_to_number(_first(properties, _COST_KEYS)),
        children=children,
        plan_node=node,
    )
    # A Bitmap Heap Scan's index name lives on its Bitmap Index Scan child.
    if normalized.operator == "Bitmap Heap Scan" and not normalized.index:
        normalized.index = next(
            (c.index for c in children if c.operator == "Bitmap Index Scan"),
            None,
        )
    relations = set()
    if normalized.relation:
        relations.add(normalized.relation)
    for child in children:
        relations |= child.relations
    normalized.relations = frozenset(relations)
    return normalized


def normalize_plan(plan, source="PostgreSQL"):
    """Return the ``NormalizedNode`` root for a PlanNode tree or raw plan."""
    return _normalize(_coerce_plan(plan, source))


def iter_plan_nodes(plan, node_id="0"):
    """Yield ``(node_id, PlanNode)`` pairs in pre-order.

    The IDs are the ones used throughout ``comparison_result``.
    """
    yield node_id, plan
    for i, child in enumerate(plan.children or []):
        yield from iter_plan_nodes(child, f"{node_id}.{i}")


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _iter_nodes(node):
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _depth(node):
    return 1 + max((_depth(child) for child in node.children), default=0)


def _join_tree(node):
    """Join order as nested tuples of relation names, e.g. ((a, b), c)."""
    if node.category == "scan" and node.relation:
        return node.relation
    parts = [p for p in (_join_tree(c) for c in node.children) if p is not None]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return tuple(parts)


def _unordered(tree):
    """Join tree with input sides ignored, for order-insensitive equality."""
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


def _top_join(root):
    return next((n for n in _iter_nodes(root) if n.category == "join"), None)


def _without_auxiliary(node):
    """Copy of the tree with auxiliary nodes replaced by their children."""
    children = []
    for child in node.children:
        children.extend(_without_auxiliary(child))
    if node.category == "auxiliary":
        return children
    copy = NormalizedNode(**{**node.__dict__, "children": children})
    return [copy]


def summarize_plan(root):
    """Counts and lists describing one normalised plan."""
    nodes = list(_iter_nodes(root))
    return {
        "node_count": len(nodes),
        "auxiliary_node_count": sum(n.category == "auxiliary" for n in nodes),
        "depth": _depth(root),
        "root_operator": root.operator,
        "operators": dict(Counter(n.operator for n in nodes)),
        "relations": sorted(root.relations),
        "scan_count": sum(n.category == "scan" for n in nodes),
        "join_count": sum(n.category == "join" for n in nodes),
        "join_order": format_join_tree(_join_tree(root)),
        "estimated_rows": root.rows,
        "estimated_cost": root.cost,
    }


# ---------------------------------------------------------------------------
# Tree edit distance (Zhang–Shasha) — structural measure for Member 4
# ---------------------------------------------------------------------------


def _rename_cost(a, b):
    """0 same operator and relation; 0.5 same kind of operator; else 1."""
    if a.relation != b.relation and (a.relation or b.relation):
        return 1.0
    if a.operator == b.operator:
        return 0.0
    if a.category == b.category:
        return 0.5
    return 1.0


def tree_edit_distance(root_a, root_b):
    """Ordered tree edit distance between two ``NormalizedNode`` trees.

    Insert and delete cost 1. Relabelling costs 0 for the same operator on
    the same relation, 0.5 for another operator of the same kind (Seq Scan vs
    Index Scan on one table, Hash Join vs Merge Join) and 1 otherwise.
    """

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
        keyroots, seen = [], set()
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
# Node matching
# ---------------------------------------------------------------------------


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _match(pg_root, ai_root):
    """Greedy relation-anchored matching between two normalised trees.

    Two nodes are candidates if they are the same kind of operator (both
    scans, both joins, both aggregates, both sorts) and the sets of base
    tables beneath them overlap. ``other`` and ``auxiliary`` nodes (Limit,
    Unique, Hash, Gather, ...) must also have the same operator. Candidates
    are ranked by:

    1. Jaccard overlap of the table sets (1.0 = same tables),
    2. same operator,
    3. same alias (separates self-joins),
    4. closest depth in the tree,

    and taken greedily so each node is matched at most once.
    """
    pg_nodes = list(_iter_nodes(pg_root))
    ai_nodes = list(_iter_nodes(ai_root))
    candidates = []
    for p in pg_nodes:
        for a in ai_nodes:
            if p.category != a.category:
                continue
            same_operator = p.operator == a.operator
            if p.category in ("other", "auxiliary") and not same_operator:
                continue
            overlap = _jaccard(p.relations, a.relations)
            if overlap == 0:
                continue
            same_alias = bool(p.alias) and p.alias == a.alias
            score = (overlap, same_operator, same_alias, -abs(p.depth - a.depth))
            candidates.append((score, p, a))

    candidates.sort(key=lambda item: item[0], reverse=True)
    used_pg, used_ai, pairs = set(), set(), []
    for score, p, a in candidates:
        if id(p) in used_pg or id(a) in used_ai:
            continue
        used_pg.add(id(p))
        used_ai.add(id(a))
        pairs.append((p, a, score[0]))

    pairs.sort(key=lambda pair: [int(part) for part in pair[0].node_id.split(".")])
    unmatched_pg = [n for n in pg_nodes if id(n) not in used_pg]
    unmatched_ai = [n for n in ai_nodes if id(n) not in used_ai]
    return pairs, unmatched_pg, unmatched_ai


def _match_dict(pairs, unmatched_pg, unmatched_ai):
    matches = []
    for p, a, overlap in pairs:
        if overlap == 1.0:
            basis = ("same tables" if p.relations else "no tables (same operator)")
        else:
            basis = f"overlapping tables ({overlap:.0%})"
        matches.append({
            "postgres": p.to_dict(),
            "ai": a.to_dict(),
            "category": p.category,
            "same_operator": p.operator == a.operator,
            "match_basis": basis,
        })
    return {
        "matches": matches,
        "unmatched_postgres": [n.to_dict() for n in unmatched_pg],
        "unmatched_ai": [n.to_dict() for n in unmatched_ai],
    }


def match_nodes(postgres_plan, ai_plan):
    """Identify corresponding nodes in the PostgreSQL and AI plans.

    Args:
        postgres_plan: ``PlanNode`` root or raw ``EXPLAIN (FORMAT JSON)``.
        ai_plan: ``PlanNode`` root or raw AI JSON.

    Returns:
        ``{"matches": [...], "unmatched_postgres": [...],
        "unmatched_ai": [...]}``. Each match is
        ``{postgres, ai, category, same_operator, match_basis}`` where
        ``postgres`` and ``ai`` are node dictionaries
        ``{node_id, operator, original_type, category, relation, relations,
        label}``. Unmatched lists contain node dictionaries.
    """
    pg_root = normalize_plan(postgres_plan, "PostgreSQL")
    ai_root = normalize_plan(ai_plan, "AI")
    return _match_dict(*_match(pg_root, ai_root))


# ---------------------------------------------------------------------------
# Difference detection
# ---------------------------------------------------------------------------


def _difference(diff_type, category, description, *, pg=None, ai=None,
                postgres_value=None, ai_value=None, explanation=None):
    return {
        "type": diff_type,
        "category": category,
        "postgres_node_id": pg.node_id if pg else None,
        "ai_node_id": ai.node_id if ai else None,
        "postgres_value": postgres_value,
        "ai_value": ai_value,
        "description": description,
        "explanation": explanation or _TYPE_EXPLANATIONS.get(diff_type, ""),
    }


_METHOD_TYPES = {
    "scan": "scan_method",
    "join": "join_method",
    "aggregate": "aggregate_method",
    "sort": "sort_method",
}


def _pair_differences(p, a):
    differences = []
    where = f" on '{p.relation}'" if p.relation else ""
    tables = ", ".join(sorted(p.relations))

    if p.operator != a.operator:
        diff_type = _METHOD_TYPES[p.category]
        subject = (f"Access to '{p.relation}'" if p.category == "scan"
                   else f"{p.category.capitalize()} over {{{tables}}}")
        differences.append(_difference(
            diff_type, p.category,
            f"{subject}: PostgreSQL uses {p.operator}, the AI plan uses "
            f"{a.operator}.",
            pg=p, ai=a, postgres_value=p.operator, ai_value=a.operator,
            explanation=(
                f"PostgreSQL's {_describe(p.operator)}; the AI's "
                f"{_describe(a.operator)}. {_TYPE_EXPLANATIONS[diff_type]}"
            ),
        ))

    if p.category == "join" and p.join_type and a.join_type \
            and p.join_type != a.join_type:
        differences.append(_difference(
            "join_type", "join",
            f"Join over {{{tables}}}: logical join type is {p.join_type} in "
            f"PostgreSQL but {a.join_type} in the AI plan.",
            pg=p, ai=a, postgres_value=p.join_type, ai_value=a.join_type,
        ))

    if p.index and a.index and p.index != a.index:
        differences.append(_difference(
            "index_used", p.category,
            f"Different index{where}: PostgreSQL uses {p.index}, the AI plan "
            f"uses {a.index}.",
            pg=p, ai=a, postgres_value=p.index, ai_value=a.index,
        ))

    if p.conditions and a.conditions and set(p.conditions) != set(a.conditions):
        differences.append(_difference(
            "condition", p.category,
            f"{p.label()}: conditions differ (PostgreSQL: "
            f"{'; '.join(p.conditions)} | AI: {'; '.join(a.conditions)}).",
            pg=p, ai=a, postgres_value=list(p.conditions),
            ai_value=list(a.conditions),
        ))

    if p.sort_key and a.sort_key and p.sort_key != a.sort_key:
        differences.append(_difference(
            "sort_key", p.category,
            f"{p.label()}: sort key is {', '.join(p.sort_key)} in PostgreSQL "
            f"but {', '.join(a.sort_key)} in the AI plan.",
            pg=p, ai=a, postgres_value=list(p.sort_key),
            ai_value=list(a.sort_key),
        ))

    if p.group_key and a.group_key and p.group_key != a.group_key:
        differences.append(_difference(
            "group_key", p.category,
            f"{p.label()}: group key is {', '.join(p.group_key)} in "
            f"PostgreSQL but {', '.join(a.group_key)} in the AI plan.",
            pg=p, ai=a, postgres_value=list(p.group_key),
            ai_value=list(a.group_key),
        ))
    return differences


def _unmatched_difference(node, side):
    in_pg = side == "postgres"
    plan_with, plan_without = (
        ("PostgreSQL's plan", "the AI plan") if in_pg
        else ("The AI plan", "PostgreSQL's plan")
    )
    if node.category == "auxiliary":
        diff_type = "auxiliary_operation"
    else:
        diff_type = "missing_operation" if in_pg else "additional_operation"
    tables = (f" over {{{', '.join(sorted(node.relations))}}}"
              if node.relations and not node.relation else "")
    description = (
        f"{plan_with} has {node.label()}{tables} with no counterpart in "
        f"{plan_without}."
    )
    if node.category == "scan" and node.relation:
        description += (
            f" '{node.relation}' is scanned fewer times, or not at all, in "
            f"{plan_without}."
        )
    return _difference(
        diff_type, node.category, description,
        pg=node if in_pg else None, ai=None if in_pg else node,
        postgres_value=node.operator if in_pg else None,
        ai_value=None if in_pg else node.operator,
        explanation=f"{_describe(node.operator)}. "
                    f"{_TYPE_EXPLANATIONS[diff_type]}",
    )


def _join_order_differences(pg_root, ai_root):
    pg_tree, ai_tree = _join_tree(pg_root), _join_tree(ai_root)
    if not isinstance(pg_tree, tuple) and not isinstance(ai_tree, tuple):
        return []
    pg_join, ai_join = _top_join(pg_root), _top_join(ai_root)
    if _unordered(pg_tree) != _unordered(ai_tree):
        diff_type, description = "join_order", (
            f"Different join order: PostgreSQL joins {format_join_tree(pg_tree)}, "
            f"the AI plan joins {format_join_tree(ai_tree)}."
        )
    elif pg_tree != ai_tree:
        diff_type, description = "join_input_order", (
            "Same join grouping, but the inputs of at least one join are on "
            f"opposite sides: PostgreSQL {format_join_tree(pg_tree)}, AI plan "
            f"{format_join_tree(ai_tree)}."
        )
    else:
        return []
    return [_difference(
        diff_type, "join", description, pg=pg_join, ai=ai_join,
        postgres_value=format_join_tree(pg_tree),
        ai_value=format_join_tree(ai_tree),
    )]


def _differences(pg_root, ai_root, pairs, unmatched_pg, unmatched_ai):
    differences = []
    for p, a, _ in pairs:
        differences.extend(_pair_differences(p, a))
    differences.extend(_join_order_differences(pg_root, ai_root))
    differences.extend(_unmatched_difference(n, "postgres") for n in unmatched_pg)
    differences.extend(_unmatched_difference(n, "ai") for n in unmatched_ai)
    return differences


def identify_differences(postgres_plan, ai_plan):
    """Detect and explain the differences between the two plans.

    Args:
        postgres_plan: ``PlanNode`` root or raw ``EXPLAIN (FORMAT JSON)``.
        ai_plan: ``PlanNode`` root or raw AI JSON.

    Returns:
        A list of difference dictionaries::

            {
                "type": "scan_method" | "join_method" | "aggregate_method"
                        | "sort_method" | "join_order" | "join_input_order"
                        | "join_type" | "index_used" | "condition"
                        | "sort_key" | "group_key" | "missing_operation"
                        | "additional_operation" | "auxiliary_operation",
                "category": "scan" | "join" | "aggregate" | "sort"
                            | "other" | "auxiliary",
                "postgres_node_id": str | None,
                "ai_node_id": str | None,
                "postgres_value": ...,
                "ai_value": ...,
                "description": str,   # what differs
                "explanation": str,   # why it may differ / what it means
            }
    """
    pg_root = normalize_plan(postgres_plan, "PostgreSQL")
    ai_root = normalize_plan(ai_plan, "AI")
    return _differences(pg_root, ai_root, *_match(pg_root, ai_root))


# ---------------------------------------------------------------------------
# Full comparison
# ---------------------------------------------------------------------------


def _estimates(pairs):
    rows = []
    for p, a, _ in pairs:
        if p.rows is None and p.cost is None and a.rows is None and a.cost is None:
            continue
        rows.append({
            "postgres_node_id": p.node_id,
            "ai_node_id": a.node_id,
            "label": p.label(),
            "postgres_rows": p.rows,
            "ai_rows": a.rows,
            "postgres_cost": p.cost,
            "ai_cost": a.cost,
        })
    return rows


def compare_plans(postgres_plan, ai_plan):
    """Compare a PostgreSQL QEP with an AI-generated QEP.

    Args:
        postgres_plan: ``PlanNode`` root or the raw ``EXPLAIN (FORMAT JSON)``
            output returned by ``get_postgres_plan``.
        ai_plan: ``PlanNode`` root or raw AI JSON accepted by
            ``parse_ai_plan``.

    Returns:
        ``comparison_result`` dictionary::

            {
                "matches": [...],              # see match_nodes
                "unmatched_postgres": [...],
                "unmatched_ai": [...],
                "differences": [...],          # see identify_differences
                "difference_counts": {type: count},
                "tree_edit_distance": float,   # helper nodes excluded
                "postgres_summary": {...},     # see summarize_plan
                "ai_summary": {...},
                "estimates": [...],            # rows/cost of matched nodes
                "identical": bool,
                "notes": [str, ...],
            }

        The difference rate is computed from this result by Member 4's
        ``calculate_difference_rate``.
    """
    pg_root = normalize_plan(postgres_plan, "PostgreSQL")
    ai_root = normalize_plan(ai_plan, "AI")

    pairs, unmatched_pg, unmatched_ai = _match(pg_root, ai_root)
    differences = _differences(pg_root, ai_root, pairs, unmatched_pg, unmatched_ai)

    pg_core = _without_auxiliary(pg_root)
    ai_core = _without_auxiliary(ai_root)
    if len(pg_core) == 1 and len(ai_core) == 1:
        distance = tree_edit_distance(pg_core[0], ai_core[0])
    else:  # a plan made only of helper nodes; fall back to the full trees
        distance = tree_edit_distance(pg_root, ai_root)

    result = _match_dict(pairs, unmatched_pg, unmatched_ai)
    result.update({
        "differences": differences,
        "difference_counts": dict(Counter(d["type"] for d in differences)),
        "tree_edit_distance": distance,
        "postgres_summary": summarize_plan(pg_root),
        "ai_summary": summarize_plan(ai_root),
        "estimates": _estimates(pairs),
        "identical": not differences,
        "notes": [
            "The AI plan is a proposed plan; it is not executed and its row "
            "and cost estimates are not based on PostgreSQL statistics.",
            "Helper nodes (Hash, Materialize, Memoize, Gather, Gather Merge, "
            "Bitmap Index Scan) are reported as auxiliary_operation and "
            "excluded from tree_edit_distance.",
        ],
    })
    return result


def format_comparison(result):
    """Render a ``compare_plans`` result as plain text (CLI / debugging)."""
    pg, ai = result["postgres_summary"], result["ai_summary"]
    lines = [
        f"{'':<16}{'PostgreSQL':<36}AI",
        f"{'Nodes':<16}{pg['node_count']:<36}{ai['node_count']}",
        f"{'Depth':<16}{pg['depth']:<36}{ai['depth']}",
        f"{'Root':<16}{pg['root_operator']:<36}{ai['root_operator']}",
        f"{'Joins':<16}{pg['join_count']:<36}{ai['join_count']}",
        f"{'Join order':<16}{pg['join_order']:<36}{ai['join_order']}",
        "",
        f"Matched nodes: {len(result['matches'])}  |  only in PostgreSQL: "
        f"{len(result['unmatched_postgres'])}  |  only in AI: "
        f"{len(result['unmatched_ai'])}  |  tree edit distance: "
        f"{result['tree_edit_distance']:g}",
        "",
        "Matches:",
    ]
    for m in result["matches"]:
        mark = "=" if m["same_operator"] else "≠"
        lines.append(
            f"  [{m['postgres']['node_id']}] {m['postgres']['label']}  {mark}  "
            f"[{m['ai']['node_id']}] {m['ai']['label']}"
        )
    lines.append("")
    lines.append("Differences:")
    if result["differences"]:
        for d in result["differences"]:
            lines.append(f"  - [{d['type']}] {d['description']}")
            lines.append(f"      Why: {d['explanation']}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.extend(f"Note: {note}" for note in result["notes"])
    return "\n".join(lines)
