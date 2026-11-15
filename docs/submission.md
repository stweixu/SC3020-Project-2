# Four-file application setup

## Application files

Copy these four files from `src/` into the same submission directory:

```text
project.py          Entry point and orchestration
interface.py        Desktop GUI and tree visualisation
comparison.py       Matching, findings, tree distance and difference rate
preprocessing.py    PlanNode, configuration, retrieval and parsing
```

The four files are the complete project-local Python runtime. Neither a `src/`
package nor a `config/` directory is required beside them. The repository retains
its test/evaluation scripts for development and experiments; those scripts are
not imported by the standalone application.

Provide dependency/setup instructions and the blank `.env.example` template in
accordance with the submission rules. `requirements.txt` lists installed packages;
it also includes pytest for development tests. Do not copy a personal `.env` or
virtual environment into the shared submission. No executable build is needed to
launch this Python application.

## Configuration

Copy `.env.example` to `.env` beside the four modules and configure the target
database's host, port, name, user and password. PostgreSQL must already be running
and contain the dataset to query. Enter the provider key/model locally to enable
AI generation. Reading configuration or launching the app does not call the API.

Configuration lookup order:

1. Existing environment variables take precedence over file values.
2. Load `.env` beside `preprocessing.py` when present.
3. For the development layout `src/` with a parent `requirements.txt`, use the
   repository-root `.env` if no `src/.env` exists.

The current working directory's unrelated `.env` is not loaded. Configuration is
loaded at import/startup; restart after editing it. No database credentials are
embedded in the Python source. Use the same interpreter/environment in PyCharm
as in the terminal.

## Windows setup (PowerShell)

From the standalone folder, using Python with Tcl/Tk installed:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with the Windows machine's database settings. Then:

```powershell
.\.venv\Scripts\python.exe project.py
```

When testing the repository rather than a standalone copy, the last command is
`.\.venv\Scripts\python.exe src\project.py`. See the root README for the TPC-H
database setup; the application itself can use another PostgreSQL schema.

## macOS setup

From the standalone folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, then run `.venv/bin/python project.py`. In the development repository,
continue to use `.venv/bin/python src/project.py` or
`.venv/bin/python -m src.project`.

## Verification

1. Launch from the actual submission folder with the four modules present.
2. Load the labelled demo and check both trees, node selection and zoom/scrolling.
3. Configure the database and fetch a PostgreSQL-only plan.
4. Open a separately supplied successful evaluation pair, if available, and check
   score/colours/explanations. Saved JSON uses plain data and does not depend on
   the previous location of the PlanNode class.
5. With API access configured, test one PostgreSQL + AI request. This makes a
   provider request; demo/replay and PostgreSQL-only mode do not.
6. Check error recovery and verify Windows operation on a Windows machine.

Repository packaging tests: `python -m pytest tests/test_submission.py -q`.
These exercise a four-file copy, configuration lookup and GUI launch without
network calls, but reuse installed dependencies. Final clean-install and Windows
checks remain separate.

## Import migration for teammates

In repository scripts, use `from src.preprocessing import PlanNode, DB_CONFIG`.
In a standalone submission module, use `from preprocessing import PlanNode,
DB_CONFIG`. The old `src.plan_node` and `config.config` modules have been removed.
Use a single import style consistently in a process, as the application does;
do not mix package and standalone module names in one interpreter.
