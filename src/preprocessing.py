
"""PostgreSQL and AI-generated QEP retrieval and parsing."""

import json

import psycopg

from config.config import DB_CONFIG
from src.plan_node import PlanNode


import json
import os
from collections.abc import Mapping

from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.plan_node import PlanNode


DEFAULT_LLM_MODEL = "gemini-3.8-flash"
DEFAULT_LLM_TIMEOUT_MS = 30000
DEFAULT_LLM_RETRY_ATTEMPTS = 2
MAX_PLAN_DEPTH = 64
MAX_PLAN_NODES = 2000


class AIPlanError(RuntimeError):
    """Raised when the AI provider fails or returns an unusable plan."""


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
    """Generate a proposed QEP and convert it to the common PlanNode tree.

    ``schema`` should contain the table/column/type/index context for the
    database. It may be a DDL string or a JSON-serializable mapping. The SQL is
    sent to the model as prompt text; it is never executed by this function.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty string")
    if isinstance(schema, str):
        schema_text = schema
    elif isinstance(schema, Mapping):
        try:
            schema_text = json.dumps(schema, indent=2, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("schema must be JSON-serializable") from exc
    else:
        raise ValueError("schema must be a DDL string or mapping")
    if not schema_text.strip():
        raise ValueError("schema must not be empty")

    load_dotenv()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise AIPlanError(
            "LLM_API_KEY is missing. Add your Gemini API key to .env or the environment."
        )

    prompt = f"""Propose a plausible PostgreSQL-style query execution plan for this SQL.

This is an educational estimate, not a real PostgreSQL EXPLAIN result. Use only
tables, columns, and indexes in the supplied database context. Do not invent
relations. This model has no access to live table statistics: set estimated
values to null if they cannot be inferred, and never claim estimates came from
PostgreSQL. Return only one JSON object in this exact shape:
{{
  "node_type": "operator name",
  "properties": {{
    "relation_name": null,
    "alias": null,
    "scan_type": null,
    "join_type": null,
    "condition": null,
    "estimated_rows": null,
    "estimated_cost": null
  }},
  "children": []
}}
Each child must have the same three keys. Put scans at leaves. Keep children
in the logical input order. Use JSON null, not the string "null", for unknown
values. Do not include markdown fences or explanation outside the JSON.

DATABASE CONTEXT:
{schema_text}

SQL QUERY:
{sql}
"""

    model = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)
    try:
        timeout_ms = int(os.getenv("LLM_TIMEOUT_MS", DEFAULT_LLM_TIMEOUT_MS))
        retry_attempts = int(
            os.getenv("LLM_RETRY_ATTEMPTS", DEFAULT_LLM_RETRY_ATTEMPTS)
        )
    except ValueError as exc:
        raise ValueError("LLM_TIMEOUT_MS and LLM_RETRY_ATTEMPTS must be integers") from exc
    if timeout_ms <= 0 or retry_attempts < 1:
        raise ValueError("LLM_TIMEOUT_MS must be positive and LLM_RETRY_ATTEMPTS at least 1")

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(
                    attempts=retry_attempts,
                    initial_delay=1,
                    max_delay=2,
                ),
            ),
        )
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise AIPlanError(f"Gemini API request failed: {exc}") from exc

    response_text = getattr(response, "text", None)
    if not response_text:
        raise AIPlanError("Gemini returned an empty response")
    return parse_ai_plan(response_text)



def parse_ai_plan(raw_plan):
    """Validate raw AI JSON and build a ``PlanNode`` tree.

    Accepted inputs are a JSON string/bytes, a decoded mapping, or an existing
    PlanNode. A top-level ``{"plan": ...}`` wrapper is also accepted.
    """
    if isinstance(raw_plan, PlanNode):
        return raw_plan
    try:
        if isinstance(raw_plan, bytes):
            raw_plan = raw_plan.decode("utf-8")
        payload = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIPlanError(f"AI plan is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise AIPlanError("AI plan root must be a JSON object")
    if "plan" in payload and isinstance(payload["plan"], Mapping):
        payload = payload["plan"]

    node_count = 0

    def build_node(item, depth=0):
        nonlocal node_count
        node_count += 1
        if node_count > MAX_PLAN_NODES:
            raise AIPlanError(f"AI plan exceeds {MAX_PLAN_NODES} nodes")
        if depth > MAX_PLAN_DEPTH:
            raise AIPlanError(f"AI plan exceeds maximum depth {MAX_PLAN_DEPTH}")
        if not isinstance(item, Mapping):
            raise AIPlanError("Each plan node must be a JSON object")

        node_type = item.get("node_type")
        if not isinstance(node_type, str) or not node_type.strip():
            raise AIPlanError("Each plan node needs a non-empty node_type")
        properties = item.get("properties", {})
        children = item.get("children", [])
        if not isinstance(properties, Mapping):
            raise AIPlanError(f"properties for {node_type!r} must be an object")
        if not isinstance(children, list):
            raise AIPlanError(f"children for {node_type!r} must be an array")
        parsed_children = [build_node(child, depth + 1) for child in children]
        return PlanNode(
            node_type=node_type.strip(),
            properties=dict(properties),
            children=parsed_children,
        )

    try:
        return build_node(payload)
    except AIPlanError:
        raise
    except (TypeError, ValueError) as exc:
        raise AIPlanError(f"AI plan could not be converted to PlanNode: {exc}") from exc
