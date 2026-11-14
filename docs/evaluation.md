# Difference-rate methodology and experimental evaluation

## Current scope

The collector saves reusable inputs; `tests/evaluate_plans.py` compares them
offline using Member 3's implementation and Member 4's implemented metric.
Collection records remain `not_evaluated`; results are written separately.
The GUI displays the main percentage. Experimental tables include that percentage
and tree-edit distance in separate columns. Live AI experiments still require a
working Gemini key and verified model. Synthetic tests are not experimental results.

## Collect plans

From the repository root with the virtual environment active:

```sh
# All 20 PostgreSQL plans; no API requests.
python tests/collect_plans.py

# A smaller pilot run.
python tests/collect_plans.py --queries Q01 Q02 Q03

# Later, with API access configured: both sources for the same workload.
python tests/collect_plans.py --source both

# Three AI generations per query, once repeated sampling has been agreed.
python tests/collect_plans.py --source ai --repeats 3
```

By default the collector reads accessible user-schema columns, constraints and
indexes from the connected database, without reading table contents.
`--schema path/to/context.sql` supplies an explicit context instead.
`--output path/to/runs` changes the output parent directory. Database connection
settings still come from `.env`. Every invocation creates a separate UTC/UUID
run directory, so an earlier collection is not overwritten.

Even AI-only runs connect to PostgreSQL to record the database environment.
The collector uses read-only transactions, a 10-second connection timeout and
15-second PostgreSQL statement timeouts. It requests estimated plans with
`EXPLAIN (FORMAT JSON)`, not `EXPLAIN ANALYZE`.

## Saved files

```text
artifacts/plan_runs/<run-id>/
    run.json
    workload.json
    schema_context.sql
    preprocessing_snapshot.py
    summary.json
    records/
        Q01_postgres_01.json
        Q01_ai_01.json          # only when requested
        ...
```

- `run.json`: collection time, selected sources/queries, repetition count,
  declared dataset, actual PostgreSQL version, planner settings, visible-schema
  indexes, estimated table counts and analysis timestamps, Python/package
  versions, schema/source hashes, and requested model/settings when applicable.
- `workload.json`: the manifest and exact selected SQL with query metadata.
- `schema_context.sql`: the exact context supplied to the AI integration. Despite
  the historical extension, automatic context is JSON text, not executable SQL.
- `preprocessing_snapshot.py`: the integration source, including its prompt
  template. Its hash identifies the version used. This is an archival copy,
  not a replacement application module to execute.
- Each record: SQL/hash, category, repetition, times, status, saved plan, error
  information if applicable, and a null difference rate.
- `summary.json`: source/status counts and paths to each record. Updated after
  every record. An interrupted run remains `in_progress` and preserves completed
  files; there is no automatic resume or retry of the whole run.

PostgreSQL records preserve the original decoded JSON response and a serialised
`PlanNode`. AI records currently preserve only the returned `PlanNode`, because
Member 2's function generates and parses the response internally. They explicitly
set `raw_response_available` to false. Capturing the exact provider response,
resolved model version and provider request metadata requires an extension to
that integration. Do not describe these records as raw AI responses.

Configuration is selected explicitly rather than copying `.env`. Credentials
are not included in run metadata; configured keys/passwords are redacted from
stored error messages. Generated runs are ignored by Git. Share selected complete
run folders separately when the team needs to inspect them.

### Status definitions

- `success`: a plan was returned and serialised. This does not certify SQL/plan
  semantic equivalence or the validity of an AI execution strategy.
- `retrieval_failed`: PostgreSQL plan retrieval failed.
- `generation_failed`: the AI wrapper failed, including provider errors or its
  internal JSON validation/parsing. Raw malformed AI responses are not available.
- `parsing_failed`: a returned payload could not be converted/serialised. A raw
  PostgreSQL payload is retained if one was retrieved.
- `not_evaluated`: comparison has not run. Never interpret this as 0% difference.

The command exits nonzero on setup failure or any failed record. Later queries
continue after individual retrieval/parsing failures. Recorded durations measure
collection and parsing, not query execution time or comparator performance.

## Procedure for the final experiment

1. Freeze the workload version, database contents/indexes and AI context. Run
   `ANALYZE` after loading data, then avoid setup changes during collection.
2. Agree with Member 2 on the accessible model and prompt. Verify the context
   describes the actual indexes. The source snapshot records the prompt template;
   exact SQL and context are stored alongside it.
3. Run a small pilot, inspect both plans, and resolve generation/parsing issues.
4. Choose sampling before the final run. A one-generation-per-query baseline is
   economical but descriptive only. If using three generations, apply it
   consistently rather than rerunning only examples with undesirable outcomes.
5. Collect all 20 queries. With `--source both`, each repetition retrieves a
   PostgreSQL plan and then requests its AI counterpart. SDK retries within one
   API call are not additional experimental repetitions.
6. Evaluate the collection using the commands below. `restore_plan(record)`
   rebuilds trees without database/API calls. Keep comparison outputs as separate
   derived artifacts; retain the original inputs.
7. Pair by query ID **and exact SQL/hash**, checking schema context, workload and
   database configuration. Query IDs alone are insufficient across different
   runs. If collecting AI later against earlier PostgreSQL inputs, confirm the
   database/context have not changed; otherwise recollect both sources.
8. Report per-query scores, counts and failure rates. For repeated generations,
   summarise within each query first, then across queries; report sample counts.
   State how failures affect the denominator and never silently drop them.

No explicit temperature or seed is currently supplied by the AI wrapper, so
metadata records these as provider defaults/unset. The code and context snapshots
support traceability, not a guarantee of identical future provider outputs.

## Offline evaluation

```sh
# A collection containing both sources; replace <run-id> with the actual folder.
python tests/evaluate_plans.py artifacts/plan_runs/<run-id>

# Separate compatible runs, with an explicitly reused PostgreSQL baseline.
python tests/evaluate_plans.py artifacts/plan_runs/<ai-run> --postgres-run artifacts/plan_runs/<pg-run> --postgres-repeat 1
```

One-generation-per-query is the economical initial experiment (20 AI requests,
excluding SDK retries); use a three-query pilot first to validate the configured
model and inspect the output. Additional repetitions are optional, but choose
the policy before collecting final results. The pilot and final collection are
separate runs; do not selectively combine favourable outputs.

The evaluator pairs records by query ID, repetition, and exact SQL/hash. It
rejects duplicate records, SQL/category mismatches, and changed recorded database,
dataset, schema-context or preprocessing metadata across runs. Baseline reuse is
explicit, never inferred. Old DDL-context runs will not match new automatic-context
runs: recollect both sources together for the final experiment. Metadata equality
cannot prove that the underlying data remained unchanged.

Each evaluation creates a new directory under `artifacts/evaluations/`:

- `evaluation.json`: source run metadata, pairing policy, metric version and
  comparator source hash.
- `comparison_snapshot.py`, `evaluation_snapshot.py`: exact implementation copies.
- `pairs/*.json`: original-record paths/hashes, status, replayable trees, complete
  comparator output, score/count breakdown and tree-edit distance.
- `results.csv`: one row per planned query/repetition, including failed pairs.
- `queries.csv`: per-query means, min/max, sample standard deviation and counts.
- `summary.json`: overall/category macro-averages, finding counts and failure counts.
- `results.md`: readable tables and low/high-score examples for manual inspection.

The source collections are not changed. Output directories cannot be overwritten.
Completed pair files survive interruption; `summary.json` stays `in_progress`
until all exports finish. A new invocation creates a fresh evaluation rather than
resuming. The evaluator performs no database/API requests.

Missing source records are `missing_input`; failed collection records are
`input_failed`; invalid pairs/comparator errors are `comparison_failed`; zero
comparison units are `unavailable`. All have null percentages. Planned pairs from
`run.json` enter the failure denominator even if collection was interrupted.
The command exits nonzero if any pair is unsuccessful. A PostgreSQL-only collection
therefore produces missing-input rows, not a comparison experiment.

For repetitions, average valid scores within each query first; then take the
unweighted mean of those query means. Categories use the same procedure within
their queries. Queries with no valid pairs have no mean, and counts remain visible.
This prevents large plans or queries with more successful repetitions dominating
the overall score. It remains a success-conditional summary: systematic failures
can bias the observed mean. The reported failure rate includes missing and
unevaluable pairs, not only provider failures. Sample standard deviations are
unavailable for fewer than two observations.

## Consolidating an initial run and recovery

`tests/consolidate_results.py` combines completed evaluations offline. It keeps
every initially successful pair and accepts recovery results only for initially
unsuccessful query/repetition slots. It checks SQL identity, original-record
hashes, model/settings, recorded database/context and comparator version. It never
chooses between successful plans based on their score. Both-source collections
with matching repetition IDs are expected; keep original collection files available
because their SQL/hashes are checked even for failed evaluated pairs.

The final experiment was consolidated from:

- Initial evaluation: `artifacts/evaluations/20261114T185524_0567cc8f/`
- Recovery evaluation: `artifacts/evaluations/20261114T185821_40c5daf5/`
- Combined output: `artifacts/evaluations/final_20_queries/`

The output has already been generated. To reproduce it into a **new** directory:

```sh
python tests/consolidate_results.py artifacts/evaluations/20261114T185524_0567cc8f artifacts/evaluations/20261114T185821_40c5daf5 --output artifacts/evaluations/final_20_queries_reproduced
```

Open `final_20_queries/results.md` for the combined table, or `results.csv` in a
spreadsheet. The selected mean is **68.70% across 20 queries**, with one successful
pair per query. Initial coverage was 16/20; four AI calls failed with HTTP 429
per-minute rate limits, and a separate four-query recovery succeeded. Report the
initial failure rate as **20%**, alongside eventual coverage **20/20**. Do not
describe collection as failure-free. There were 24 recorded pair attempts across
these two runs; the Q01/Q06 pilots are excluded, as are SDK-internal retry counts.

`attempt_history.json` retains every initial/recovery evaluation record and its
collection errors. `selection.csv` identifies which run supplies each final row.
`consolidation.json` records metadata and the selection policy. Consolidated GUI
files use query IDs, e.g. `pairs/Q06_001.json`; original filenames remain intact.
Category/finding summaries are recomputed from the selected 20 results, not by
averaging the two run means. All outputs remain derived data; source files are
unchanged. The source archives and combined artifacts are ignored by Git, so
preserve/share the experiment folders deliberately when preparing the report.

Consolidation verification:

```sh
python -m pytest tests/test_consolidation.py tests/test_evaluation.py -q
```

The table is ready for analysis. Scores are detected disagreement, not AI error
rates; inspect original conditions and operators before writing causal explanations.

## Selected difference-rate methodology

**Equal-weighted matched-node disagreement**, version
`matched-node-disagreement-v1`, implemented by `classify_comparison()` and
`calculate_difference_rate(comparison_result)` in `src/comparison.py`:

    difference_rate = 100 * (C + U_P + U_A) / (M + U_P + U_A)

- `M`: total matched pairs, including changed pairs.
- `C`: changed matched pairs, each counted once (`0 <= C <= M`).
- `U_P`, `U_A`: unmatched PostgreSQL and AI nodes respectively.

For five matched pairs, two changed pairs and one unmatched AI node, the score
is `100 * 3 / 6 = 50%`. Identical nonempty normalised trees would score 0%.
Failed/missing plans are not valid inputs; a zero denominator is unavailable,
not automatically zero.

### Counting and weighting

1. `matches` supplies `M`. Each pair is changed if `same_operator` is false or
   a scored finding references either member. Multiple findings on a pair count
   **once**, using a set of pair indices.
2. All current `differences` types are included: scan/join/aggregate/sort methods,
   join order/input order/type, index, condition, sort/group keys, and missing,
   additional or auxiliary operations. `estimates` never enters the numerator.
3. Structural findings affect only the pairs containing their referenced nodes.
   Member 3 currently references the top join of each plan for a global join-order
   finding. Thus a regrouped five-node join tree can score 1/5 = 20%, even if
   several edges changed. This measures affected reported units, not changed edges.
   If the two references belong to different matched pairs, both count.
4. `unmatched_postgres` and `unmatched_ai` supply `U_P` and `U_A`. Each unmatched
   node counts once, including helper nodes such as Hash/Gather/Materialize.
   A missing/additional-operation finding does not count it a second time.
5. Every comparison unit has weight 1; a detected change contributes 1, an
   unchanged pair contributes 0. There are no extra per-category penalties.
6. Validate one-to-one matching, no overlap between matched/unmatched nodes,
   node totals against available summaries, and all finding references. Unknown
   difference types or findings without references raise a validation error.
   The GUI additionally checks exact coverage of the original displayed trees.

`classify_comparison()` returns the counts, percentage and node statuses;
`calculate_difference_rate()` returns just the percentage. Neither modifies the
comparator output. The GUI and evaluator call the same classification, preventing
disagreement between red/blue/purple units and the percentage. Green means
**no detected change**, not proven equivalence. A matched pair is one unit despite
occupying one node in each tree; counting coloured boxes across both trees would
incorrectly count pairs twice.

### Why this method

The goal is to summarise the extent of detected plan disagreement, including
properties. Equal weighting is transparent without pretending to estimate runtime
impact: a scan change can matter more than a join change depending on the data.
Deduplication prevents verbose findings from inflating a pair's contribution.
It uses Member 3's normalisation, matching, findings and original-tree node IDs;
it does not replace those algorithms. It is a project-specific disagreement
measure, not a universal industry standard or a proven mathematical distance.

Alternatives considered:

| Method | Reason not selected as the sole primary percentage |
|---|---|
| Ordered tree-edit distance | Established tree-comparison method, but Member 3's labels omit condition/key properties and normally remove helpers. Percentage normalisation would require an additional design choice. |
| Operator-set/Jaccard similarity | Loses node arrangement and can miss join-order and property differences. |
| Unequal category weights | No runtime evidence justifies fixed importance weights for joins versus scans, etc. |
| Cost/runtime ratio | Measures a different objective; AI estimates are not calibrated to PostgreSQL's statistics or cost units. |

### Supporting tree-edit distance

Keep Member 3's `tree_edit_distance` as a separate result column and use it when
interpreting differences. Her ordered Zhang–Shasha implementation uses insertion/
deletion cost 1 and relabelling costs 0, 0.5 or 1 based on operator/category/relation.
Helper nodes are normally removed (full-tree fallback for helper-only cases).
Condition-only changes can yield 100% disagreement for a single scan, but TED 0.
An omitted Hash helper can also increase the main score without increasing TED.

Do not add TED to the percentage or interpret raw TED as a percentage. It depends
on tree size. In the report, explain each representative pair using the actual
findings, operator trees and auxiliary-node count rather than assuming that a
larger number establishes worse execution performance.

### References and report use

- Zhang, K. and Shasha, D. (1989), *Simple Fast Algorithms for the Editing Distance
  Between Trees and Related Problems*, SIAM Journal on Computing 18(6), 1245–1262.
  [DOI](https://doi.org/10.1137/0218082).
- [Zhang–Shasha Python documentation](https://zhang-shasha.readthedocs.io/en/latest/)
  describes insert/remove/update costs for ordered tree comparison. The project
  uses Member 3's implementation, not the `zss` package.
- [PostgreSQL: Using EXPLAIN](https://www.postgresql.org/docs/17/using-explain.html)
  explains plan nodes and the planner's cost units.

Include the formula, exact counting rules, rationale, worked example, input
normalisation/matching approach and limitations in the methodology section of
the final report. Use the generated tables for results, then manually discuss
representative cases. These documentation sections are report material, not a
claim that the real AI experiment has already been performed.

## Limitations to carry into the report

- Agreement with PostgreSQL does not prove optimality, correctness or speed.
  Neither these plans nor the score measure actual query execution performance.
- TPC-H SF1 with primary-key indexes is one environment. The workload does not
  prove generality across schemas, scales or index configurations.
- Category sizes are unequal. Q02/Q03 are one controlled selectivity family.
  Report category counts and the averaging method.
- AI generation may vary, and the AI does not receive PostgreSQL's full planner
  statistics/configuration. Schema-valid output can still be semantically wrong.
- Formatting and abstraction differences may inflate scores unless normalisation
  is consistently defined. Matching errors can distort both colours and metrics.
- Equal node weights ignore differences in the runtime importance of operators.
  Tree size and the inclusion of implementation-detail nodes affect the score.
- Member 3's matching is greedy and table-set based. Self-joins, repeated operators
  and ambiguous matches can distort correspondence. This layer uses that matching
  rather than independently proving correspondence.
- Property comparisons usually require both plans to supply a value. Omission is
  not scored as a property difference. No detection does not prove equality.
- Condition normalisation lowercases text and strips quotes, casts, parentheses
  and qualifiers. It can both flag equivalent expressions and hide meaningful
  string-case, type or Boolean-grouping differences. Quoted relation identifiers
  are also lowercased. These are inherited comparator limitations, not corrected
  by the percentage; retain original properties for manual inspection.
- Current AI capture is parsed-only. Row counts saved in metadata are statistical
  estimates, not verified dataset counts. No dataset checksum is captured.
- API timeouts or malformed output are collection failures, not plan disagreements.

Add observed experimental failure cases after live collection.
The collector, evaluator, metric, tests and this procedure were prepared with OpenAI
GPT-6 Astra assistance through OpenCode; include this in the final acknowledgement.
