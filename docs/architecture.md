# Architecture

Application flow:

SQL query -> project.run_query -> preprocessing (PostgreSQL QEP + AI QEP) -> comparison -> interface

`src/project.py` orchestrates retrieval, parsing, comparison and the main metric.
Both QEPs use `PlanNode` from `src/preprocessing.py`. Blank AI context is populated
from the connected database; users may supply their own context instead.

Member 3's comparator normalises and matches the plans without replacing the
original displayed trees. Member 4's `classify_comparison()` validates that output
and calculates the matched-node disagreement rate and colour classifications.
`calculate_difference_rate()` exposes the percentage alone. The GUI uses the
same classification rules. Tree-edit distance is a separate supporting measure.

`tests/collect_plans.py` saves immutable experiment inputs. `tests/evaluate_plans.py`
replays them offline, saves complete findings/counts and exports summary tables.
The GUI can open the evaluator's saved pair records. See `comparison.md`,
`evaluation.md` and `gui.md` for the contracts and limitations.

## Four-file layout

`preprocessing.py` owns `PlanNode`, environment-file loading, `DB_CONFIG` and
input retrieval/parsing. `comparison.py` and `interface.py` import that shared
class; `project.py` orchestrates the other modules. Preprocessing does not import
any of them, so there is no circular dependency.

Package-relative imports support `python -m src.project`; sibling imports support
`python src/project.py` and a standalone four-file folder launched with
`python project.py`. Environment-file lookup is based on the module location,
not the terminal's current directory. See `submission.md` for packaging details.
