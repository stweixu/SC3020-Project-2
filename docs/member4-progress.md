# Member 4 progress

Status after Member 3 integration, the metric and offline evaluator implementation.
The implementation and a 20-query live AI experiment are now available. Final
interpretation/reporting and Windows checks remain. See `evaluation.md` and
`gui.md` for usage and methodology.

## A. Difference Rate & Evaluation

| Task | Current state | Remaining work |
|---|---|---|
| Research and propose a suitable difference-rate methodology | Selected equal-weighted matched-node disagreement; formula, rationale, references, alternatives and examples in `evaluation.md`. | Transfer into the team's final report. |
| Work with Member 3 to ensure the metric uses comparison results correctly | Incorporated her comparator/docs/tests from `origin/main`; implemented validated adapter using her original-tree IDs. Controlled tests cover actual output. | Review unexpected cases in live AI results. |
| Determine how differences should be weighted or counted | Implemented `calculate_difference_rate()` and shared `classify_comparison()` in `comparison.py`. Includes deduplication, referenced structural units and all unmatched nodes; excludes estimates/TED. | No remaining metric implementation dependency. |
| Prepare diverse SQL test queries | Completed initial workload: 20 custom queries with metadata; all plan successfully in the local PostgreSQL database. | Review coverage as a group; version any changes. |
| Run experiments and collect comparison results | Completed initial 20-query collection/evaluation and recovery of four rate-limited queries; 20 successful final pairs consolidated with original failure history. | Preserve source artifacts for the report/submission. |
| Analyse results across different queries | Consolidated per-pair/query CSV, category summaries, findings and recovery history generated. Selected 20-query mean is 68.70%. | Inspect and interpret representative examples; final discussion remains. |
| Discuss limitations of the comparison methodology and metric | Greedy matching, condition normalisation, missing properties, auxiliary nodes, score interpretation and collection limitations documented. | Add observed experimental failures/results. |

## B. GUI & Visualisation

| Task | Current state | Remaining work |
|---|---|---|
| Research and select a suitable Python GUI framework | Completed: Tkinter/ttk with Canvas; rationale in `gui.md`. | Verify the final packaged application on Windows. |
| Implement SQL query input | Completed; connected to retrieval with loading/error states. | Final end-to-end user checks. |
| Display PostgreSQL and AI QEPs as trees | Both renderers implemented; live PostgreSQL, controlled AI trees and saved evaluation replay supported. | Verify actual API-generated plans in the GUI. |
| Highlight corresponding differences using colour coding | Connected to Member 3's output with the shared metric classifications; condition and structural findings affect matched pairs. | Review highlighting on live AI pairs. |
| Display textual explanations and difference rate | Real descriptions/explanations, linked similarities, percentage and count breakdown connected and tested. | Capture final screenshots using real plan pairs. |

## Additional implementation and verification

- Automatic database context in `preprocessing.py` reads accessible schemas,
  columns, constraints and indexes; explicit GUI/context-file overrides remain.
- `Open saved result…` displays evaluator pair records without new provider calls.
- `tests/validate_generality.py` exercises a small alternate schema and rolls back
  its schema/data changes. It validates context/parsing/self-comparison/presentation,
  not AI generation or complete semantic correctness.
- The old `test_db.py` connected during test discovery with a hard-coded role.
  It now uses the configured application connection inside an actual test.

### Local verification (14 November 2026)

- Full `python -m pytest -q`: **77 passed**, including Tk interaction and existing
  live PostgreSQL tests; no real AI provider requests.
- Direct `python src/project.py` entry-point smoke check passed; the verification
  harness closed the window automatically.
- Alternate-schema validation passed for three queries (1, 5 and 3 plan nodes);
  confirmed its temporary schema was removed by rollback.
- All 20 previously collected PostgreSQL plans (89 nodes total) self-compared at
  0% with complete GUI node coverage. These are identity checks, not AI results.
- Evaluating the PostgreSQL-only collection produced 20 `missing_input` rows,
  zero valid pairs and unavailable percentages, as intended. Diagnostic output:
  `artifacts/evaluations/20261114T145252_4da6e18d/` (ignored by Git).
- `git diff --check` passed. Windows and actual Gemini generation remain untested.

## Next dependencies

1. Consolidated results are in `artifacts/evaluations/final_20_queries/results.md`.
   The user completed Q01/Q06 live collection pilots and verified saved results in
   the GUI. The 20-query batch initially had four HTTP 429 failures; all four were
   successfully recovered. Pilot scores are excluded from the consolidated table.
2. Representative-case analysis and final report/screenshots. A direct live GUI
   request still needs explicit verification; saved-result replay has been checked.
3. Windows verification on a Windows machine; local checks use macOS.
4. Part C's final packaging/clean-machine launch checks and consolidated report.

The selected Member 3 files were incorporated without merging all remote changes,
committing or pushing. Local Member 4 work remains on `member4-gui-evaluation`.
