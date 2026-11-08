"""Main application orchestration and command-line entry point."""

from pathlib import Path

from src.comparison import compare_plans
from src.interface import display_comparison
from src.preprocessing import get_ai_plan, get_postgres_plan


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"


def run_query(sql, schema=None, *, display=True):
    """Run the full QEP comparison workflow for one SQL query.

    Args:
        sql: SQL query entered by the user.
        schema: Database schema context for the LLM. Defaults to the repository
            TPC-H schema file.
        display: If true, send the plans and comparison result to the UI layer.

    Returns:
        A dictionary containing the PostgreSQL plan, AI plan, and comparison.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Please provide a non-empty SQL query.")

    if schema is None:
        try:
            schema = DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Could not read database schema at {DEFAULT_SCHEMA_PATH}: {exc}"
            ) from exc

    try:
        postgres_plan = get_postgres_plan(sql)
    except NotImplementedError as exc:
        raise RuntimeError(
            "PostgreSQL plan retrieval is not implemented yet; see Member 1's "
            "get_postgres_plan(sql) task."
        ) from exc

    ai_plan = get_ai_plan(sql, schema)

    try:
        comparison_result = compare_plans(postgres_plan, ai_plan)
    except NotImplementedError as exc:
        raise RuntimeError(
            "Plan comparison is not implemented yet; see Member 3's "
            "compare_plans(postgres_plan, ai_plan) task."
        ) from exc

    if display:
        try:
            display_comparison(postgres_plan, ai_plan, comparison_result)
        except NotImplementedError as exc:
            raise RuntimeError(
                "The comparison display is not implemented yet; see Member 4's "
                "display_comparison(...) task."
            ) from exc

    return {
        "postgres_plan": postgres_plan,
        "ai_plan": ai_plan,
        "comparison_result": comparison_result,
    }


def main():
    """Prompt for SQL, run the pipeline, and report actionable errors."""
    try:
        sql = input("Enter SQL query: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("No query submitted.")
        return 1

    if not sql:
        print("Please enter a non-empty SQL query.")
        return 1

    try:
        run_query(sql)
    except Exception as exc:
        print(f"Could not complete the QEP comparison: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
