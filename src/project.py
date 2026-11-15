"""Application orchestration and desktop entry point."""

from pathlib import Path
import json

# Relative imports support package mode; sibling imports support a four-file copy.
if __package__:
    from .comparison import DIFFERENCE_RATE_VERSION, classify_comparison, compare_plans
    from .interface import ComparisonApp, display_comparison
    from .preprocessing import (
        AIPlanError, DB_CONFIG, get_ai_plan, get_database_context, get_postgres_plan,
        parse_ai_plan, parse_postgres_plan,
    )
else:
    from comparison import DIFFERENCE_RATE_VERSION, classify_comparison, compare_plans
    from interface import ComparisonApp, display_comparison
    from preprocessing import (
        AIPlanError, DB_CONFIG, get_ai_plan, get_database_context, get_postgres_plan,
        parse_ai_plan, parse_postgres_plan,
    )


def run_query(sql, schema=None, *, display=True, mode="both"):
    """Run the full QEP comparison workflow for one SQL query.

    Args:
        sql: SQL query entered by the user.
        schema: Database context for the LLM. If blank/None, inspect the connected
            database's accessible schemas, constraints and indexes.
        display: If true, send the plans and comparison result to the UI layer.
        mode: "postgres" retrieves only PostgreSQL; "both" also requests AI.

    Returns:
        A dictionary containing the PostgreSQL plan, AI plan, and comparison.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Please provide a non-empty SQL query.")
    if mode not in {"postgres", "both"}:
        raise ValueError("mode must be 'postgres' or 'both'.")

    postgres_plan = parse_postgres_plan(get_postgres_plan(sql))
    ai_plan = None
    comparison_result = None
    notice = "PostgreSQL plan loaded. AI generation and comparison were not requested."
    if mode == "both":
        try:
            if schema is None or (isinstance(schema, str) and not schema.strip()):
                schema = get_database_context()
            ai_plan = get_ai_plan(sql, schema)
        except Exception as exc:
            notice = f"PostgreSQL plan loaded; AI generation failed: {exc}"
        else:
            try:
                comparison_result = compare_plans(postgres_plan, ai_plan)
                comparison_result["metric"] = classify_comparison(comparison_result)
                comparison_result["difference_rate"] = comparison_result["metric"]["difference_rate"]
                notice = "Plans loaded. Comparison results are shown below."
            except NotImplementedError:
                notice = "Both plans loaded. Comparison is not implemented yet; colours and score are unavailable."
            except Exception as exc:
                comparison_result = None
                notice = f"Both plans loaded; comparison failed: {exc}"

    if display:
        display_comparison(postgres_plan, ai_plan, comparison_result, notice=notice)

    return {
        "postgres_plan": postgres_plan,
        "ai_plan": ai_plan,
        "comparison_result": comparison_result,
        "notice": notice,
    }


def load_saved_comparison(path):
    """Open one successful evaluator pair without database or provider requests."""
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("status") != "success":
        raise ValueError("Choose a successful pair JSON from an evaluation's pairs folder.")
    if (not isinstance(record.get("metric"), dict)
            or record["metric"].get("method") != DIFFERENCE_RATE_VERSION):
        raise ValueError("Saved metric version differs; evaluate the collection again.")
    return {
        "postgres_plan": parse_ai_plan(record["postgres_plan"]),
        "ai_plan": parse_ai_plan(record["ai_plan"]),
        "comparison_result": record["comparison_result"],
        "sql": record["sql"],
        "notice": f"SAVED RESULT — {record['query_id']}, repetition {record['repetition']}. "
                  "Loaded offline from an evaluation record.",
    }


def main():
    """Launch the GUI; live retrieval runs on its background worker."""
    import tkinter as tk

    connection = (
        f"Database: {DB_CONFIG.get('dbname') or '(not configured)'}  |  "
        f"Host: {DB_CONFIG.get('host') or 'local'}:{DB_CONFIG['port']}  |  "
        "Connection settings: .env (restart after changing)"
    )
    root = tk.Tk()
    ComparisonApp(
        root,
        run_callback=lambda sql, context, mode: run_query(sql, context, display=False, mode=mode),
        load_callback=load_saved_comparison,
        connection_label=connection,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
