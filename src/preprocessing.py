
"""PostgreSQL and AI-generated QEP retrieval and parsing."""

import json

import psycopg

from config.config import DB_CONFIG
from src.plan_node import PlanNode


def get_postgres_plan(sql):
    """
    Retrieve PostgreSQL's estimated execution plan in JSON format.

    Args:
        sql: A single SQL query to explain.

    Returns:
        Raw PostgreSQL JSON execution plan.

    Note:
        EXPLAIN without ANALYZE does not execute the query.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query cannot be empty.")

    explain_sql = f"EXPLAIN (FORMAT JSON) {sql.strip()}"

    try:
        with psycopg.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute(explain_sql)
                result = cur.fetchone()

        if result is None:
            raise RuntimeError(
                "PostgreSQL did not return an execution plan."
            )

        return result[0]

    except psycopg.Error as exc:
        raise RuntimeError(
            f"Failed to retrieve PostgreSQL execution plan: {exc}"
        ) from exc


def parse_postgres_plan(raw_plan):
    """
    Convert PostgreSQL's JSON execution plan into a PlanNode tree.

    Args:
        raw_plan: Raw JSON returned by get_postgres_plan().

    Returns:
        Root PlanNode.
    """

    # Handle JSON strings as well as decoded JSON objects.
    if isinstance(raw_plan, str):
        try:
            raw_plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid PostgreSQL plan JSON.") from exc

    # EXPLAIN (FORMAT JSON) normally returns a list containing one object.
    if isinstance(raw_plan, list):
        if len(raw_plan) != 1:
            raise ValueError(
                "Expected exactly one PostgreSQL execution plan."
            )
        raw_plan = raw_plan[0]

    if not isinstance(raw_plan, dict):
        raise ValueError("Unexpected PostgreSQL execution plan format.")

    # Extract the root Plan object.
    if "Plan" in raw_plan:
        raw_plan = raw_plan["Plan"]

    def build_node(plan_data):
        if not isinstance(plan_data, dict):
            raise ValueError("Invalid plan node.")

        node_type = plan_data.get("Node Type")

        if not isinstance(node_type, str) or not node_type:
            raise ValueError("Plan node is missing its Node Type.")

        # Preserve PostgreSQL properties except node type and children.
        properties = {
            key: value
            for key, value in plan_data.items()
            if key not in ("Node Type", "Plans")
        }

        # Validate child nodes.
        raw_children = plan_data.get("Plans") or []

        if not isinstance(raw_children, list):
            raise ValueError("Invalid child plans format.")

        children = [
            build_node(child)
            for child in raw_children
        ]

        return PlanNode(
            node_type=node_type,
            properties=properties,
            children=children
        )

    return build_node(raw_plan)


# Task 2 functions — implemented by the LLM integration teammate.

def get_ai_plan(sql, schema):
    raise NotImplementedError


def parse_ai_plan(raw_plan):
    raise NotImplementedError
