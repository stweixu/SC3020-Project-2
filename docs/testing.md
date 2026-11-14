# Testing

## Evaluation workload

The workload contains **20 custom SQL queries** in `tests/queries/`. Each file
contains one complete query and can be copied directly into the GUI. Metadata in
`tests/queries/manifest.json` records stable IDs, categories, features and purposes
for a future batch evaluator.

The queries were composed for this project rather than copied from the published
TPC-H Q1–Q22 suite. They still use familiar SQL patterns and TPC-H relations;
custom authorship does not establish that an LLM has never seen similar queries.

### Coverage

| ID | Category | Main feature / comparison purpose |
|---|---|---|
| Q01 | Selection | Selective primary-key equality lookup |
| Q02 | Selection | Narrow primary-key range |
| Q03 | Selection | Broad primary-key range; controlled counterpart to Q02 |
| Q04 | Selection | Non-indexed range, IN and LIKE filters |
| Q05 | Join | Two-table join with selective predicates |
| Q06 | Join | Three-table chain with predicates on all relations |
| Q07 | Join | Four-table chain spanning small and large relations |
| Q08 | Aggregation | Multiple grouping keys and aggregate expressions |
| Q09 | Aggregation | WHERE before aggregation and HAVING afterwards |
| Q10 | Ordering | ORDER BY with LIMIT and a deterministic tie-breaker |
| Q11 | Aggregation | Multi-column DISTINCT |
| Q12 | Subquery | EXISTS and possible semi-join rewriting |
| Q13 | Subquery | NOT EXISTS and possible anti-join rewriting |
| Q14 | Subquery | Uncorrelated scalar aggregate / possible InitPlan |
| Q15 | Subquery | Correlated scalar aggregate / possible SubPlan |
| Q16 | CTE | Explicit materialisation and CTE reference |
| Q17 | Join | Outer join retaining unmatched customers |
| Q18 | Join | Self-join requiring distinct aliases for the same table |
| Q19 | Set operation | UNION ALL and separate input branches |
| Q20 | Window | Partitioned ROW_NUMBER with ordering |

All eight TPC-H tables are exercised. Q02/Q03 deliberately share query structure
to isolate the effect of the range bounds. They form one query family, not two
independent structural innovations. The other queries vary SQL structure and
semantics. Categories are unequal in size; report category counts alongside any
category or overall averages.

### Assumptions

- Use the loaded TPC-H SF1 data and `database/schema.sql`.
- The base setup has primary-key indexes only. Foreign keys do not automatically
  create indexes on their referencing columns in PostgreSQL.
- Run `ANALYZE` after loading the data, as described in the README.
- Keep database contents, indexes and planner settings consistent across a run.
- The files use unqualified table names. The connection's search path must resolve
  them to the intended TPC-H tables (normally `public`).
- This workload is intentionally dataset-specific. The application and comparison
  algorithm must still support other schemas; these queries alone do not prove
  schema independence or coverage of all SQL constructs.

## Validate the queries

From the repository root with the environment active and `.env` configured:

```sh
python tests/validate_workload.py
```

The validator reads the manifest, obtains `EXPLAIN (FORMAT JSON)` plans and passes
each through the existing PostgreSQL parser. It prints the query ID, category,
node count and operator names. A failing query does not prevent later queries
from being checked. The process exits nonzero if any query fails.

Validation uses a read-only connection and a 15-second per-statement timeout.
It does not use `EXPLAIN ANALYZE`, execute the SELECT queries, call an AI provider,
or calculate a difference rate. Success establishes that PostgreSQL can plan the
SQL against the configured schema and that the current parser accepts its plan.
It does not establish result-set correctness, nonempty results, execution speed,
or the correctness of the future comparison algorithm.

Operator choices are observations, not pass/fail criteria. They depend on data,
statistics, indexes, PostgreSQL version and settings. For example, a suitable
index does not guarantee an Index Scan; an EXISTS query need not preserve its
original subquery shape. Q10 tests ordering/limiting, but plain EXPLAIN cannot
establish the runtime sort method. Plans can encode strategy and join semantics
in properties, not just in operator names.

## Using the workload in the GUI

1. Start `python src/project.py`.
2. Open a query file under `tests/queries/` in VS Code and copy its SQL into the GUI.
3. Select **PostgreSQL only** and fetch the plan.
4. When AI access is ready, use the same SQL and database context in AI mode.

Some queries intentionally return many rows or include correlated work. Use the
plan-fetch workflow for this preparation stage, rather than executing the full
queries just to inspect their shape.

## Collection and offline evaluation

The plan collector and experimental procedure are described in
[evaluation.md](evaluation.md). Start collecting PostgreSQL inputs with:

```sh
python tests/collect_plans.py
```

Unlike validation, this saves raw PostgreSQL plans, parsed trees, SQL and run
metadata. Evaluate a both-source collection offline with:

```sh
python tests/evaluate_plans.py artifacts/plan_runs/<run-id>
```

The evaluator exports scores and separate tree-edit distances, counts, per-query
and category summaries, failures and replayable GUI records. See
[member4-progress.md](member4-progress.md) for task-by-task status.

For each query, record its ID and SQL, workload version, dataset/schema,
PostgreSQL version and settings, index configuration, AI model/settings, prompt
version, raw plans, comparison output, score and failure status. Save raw plans
so comparisons can be repeated without regenerating AI responses.

Final findings require real AI plans; the comparator and selected metric are now
integrated. Report unsuccessful generations/comparisons separately;
do not label them as 0% or silently omit them. Describe whether summaries weight
each query equally or weight query categories equally. Account for the Q02/Q03
family when interpreting claims about query diversity.

## Automated verification

```sh
# Comparator, metric, GUI, collector and evaluator; no database/API requests.
python -m pytest tests/test_comparison.py tests/test_difference_rate.py tests/test_interface.py tests/test_plan_collection.py tests/test_evaluation.py -q

# Complete suite, including the existing live PostgreSQL tests (needs .env/database).
python -m pytest -q

# Alternate schema smoke check; needs CREATE SCHEMA permission.
python tests/validate_generality.py
```

The alternate-schema command creates a uniquely named schema with products/sales,
checks context/index discovery, plans three queries, checks zero-rate self-comparison
and presentation coverage, and rolls everything back. It does not call Gemini.
The metric tests cover deduplication, condition-only changes with zero TED, unknown
properties, unmatched/helper nodes, join order and invalid contracts. Evaluator
tests cover SQL identity, failed/missing pairs, macro-averaging, explicit baseline
reuse, provenance, source preservation and saved-result replay.

Tk tests run on a graphical desktop and skip if no display is available. Windows
must still be checked on Windows: launch `python src/project.py`, compare a real
pair, select findings, zoom/scroll, load a saved result, and recover from a failed
request. macOS tests do not establish Windows compatibility.

This workload and validator were prepared with assistance from OpenAI GPT-6 Astra
through OpenCode. Include this use in the team's consolidated LLM acknowledgement.
