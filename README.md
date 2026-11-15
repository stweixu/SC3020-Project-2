# SC3020 Project 2

Compare PostgreSQL query execution plans (QEPs) with AI-generated plans.

## Requirements

- Python
- PostgreSQL 17
- Git LFS
- TPC-H SF1 dataset

## Setup

1. Clone the repository and download the LFS files:

   ```bash
   git lfs install
   git lfs pull
   ```

2. Create a PostgreSQL database named `sc3020_project2`.

3. Create the tables by running `database/schema.sql` in the database.

4. Configure your database connection in `.env` using `.env.example` as a template.

5. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

6. Add your Gemini API key to `.env` as `LLM_API_KEY` to enable AI plan
   generation. The model can be configured with `LLM_MODEL`.

7. Load the dataset:

   ```bash
   python database/load_tpch.py
   ```

8. Update PostgreSQL statistics:

   ```sql
   ANALYZE;
   ```

See [docs/ai-integration.md](docs/ai-integration.md) for the AI plan interface,
schema context, and limitations, and [docs/comparison.md](docs/comparison.md)
for how the two plans are matched and compared and the `comparison_result`
format.

## Run the application

Start the desktop interface from the repository root:

```bash
python src/project.py
```

`python -m src.project` is also supported. The interface uses Tkinter, normally
included with Python on Windows and macOS; check with `python -m tkinter`.

- **PostgreSQL only** retrieves a real plan without an AI API key.
- **PostgreSQL + AI** retrieves both plans using the configured Gemini account.
- **Load demo** displays clearly labelled synthetic comparison data without
  database or API access.

AI context is read automatically from the connected database's accessible
schemas, constraints and indexes. **AI schema context…** supplies an explicit
override; leave it blank for automatic context. Connection settings come from
`.env`. Live comparisons use Member 3's findings and the equal-weighted matched-node
disagreement percentage. **Open saved result…** displays an evaluator pair JSON
without another database/API request.

See [docs/gui.md](docs/gui.md) for controls, the comparison-result
format, verification steps, limitations, and development acknowledgement.

## Four-file submission

The application now consists of exactly four Python modules in `src/`:
`project.py`, `interface.py`, `comparison.py`, and `preprocessing.py`.
`PlanNode` and configuration loading are defined in `preprocessing.py`; no
`plan_node.py` or `config/config.py` is required.

These four files can be copied together into a standalone submission folder and
launched there with `python project.py`. Dependencies still need to be installed.
Place the local `.env` alongside the four files. In the development repository,
the existing root-level `.env` and `python src/project.py` continue to work.
Environment variables override values from `.env`; restart after changing settings.

See [docs/submission.md](docs/submission.md) for folder layout, Windows setup and
verification steps. Windows execution still needs confirmation on Windows hardware.

## Evaluation query workload

`tests/queries/` contains 20 custom TPC-H queries covering selections, joins,
aggregation, ordering, subqueries, a materialized CTE, a set operation and a
window function. `manifest.json` describes the purpose of each query.

Validate all queries against your configured PostgreSQL database:

```bash
python tests/validate_workload.py
```

This retrieves estimated plans without executing the queries or calling the AI
API. See [docs/testing.md](docs/testing.md) for coverage and evaluation guidance.

Save replayable PostgreSQL plans and run configuration:

```bash
python tests/collect_plans.py
```

Each run is saved under `artifacts/plan_runs/` (ignored by Git). AI collection is
optional via `--source ai` or `--source both` once API access is configured.
Evaluate a saved collection containing both sources:

```bash
python tests/evaluate_plans.py artifacts/plan_runs/<run-id>
```

Results under `artifacts/evaluations/` include per-query CSV/Markdown tables,
failure counts, main percentages and separate tree-edit distances. Replace
`<run-id>` with the actual collection folder. Failed/missing pairs have no score.

See [docs/comparison.md](docs/comparison.md) for Member 3's comparison algorithm,
[docs/evaluation.md](docs/evaluation.md) for sampling, saved-record formats
and the selected metric, and [docs/member4-progress.md](docs/member4-progress.md)
for completed work and remaining dependencies.

## Dataset

- Dataset: TPC-H SF1
- Database: PostgreSQL 17
- Tables: 8
- Indexes: Primary-key indexes only
- Foreign keys: Defined in `database/schema.sql`

The `data/` directory contains the eight TPC-H SF1 `.tbl` files, tracked with Git LFS.

## Project Structure

```text
database/     Schema and dataset loader
data/         TPC-H SF1 data
src/          Four application modules; configuration and PlanNode in preprocessing.py
tests/        Tests
docs/         Project documentation
```
