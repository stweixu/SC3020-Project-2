"""Verify the application works with only the four required Python modules."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
MODULES = ("project.py", "interface.py", "comparison.py", "preprocessing.py")


@pytest.fixture
def standalone(tmp_path):
    application = tmp_path / "submission with spaces"
    application.mkdir()
    for name in MODULES:
        shutil.copy2(ROOT / "src" / name, application / name)
    (application / ".env").write_text(
        "DB_HOST=file-host\nDB_NAME=fixture_database\nDB_PORT=5544\n"
        "DB_USER=fixture_user\nLLM_MODEL=fixture-model\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "unrelated working directory"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("DB_HOST=wrong-host\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("DB_", "LLM_", "GEMINI_")) and key != "PYTHONPATH"
    }
    return application, elsewhere, environment


def run_isolated(standalone, code, environment=None):
    application, elsewhere, default_environment = standalone
    script = "import sys\nsys.path.insert(0, sys.argv[1])\n" + code
    result = subprocess.run(
        [sys.executable, "-c", script, str(application)], cwd=elsewhere,
        env=environment or default_environment, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_four_file_copy_runs_comparison_and_mocked_workflow(standalone):
    run_isolated(standalone, """
import os
import preprocessing as inputs
import comparison
import interface
import project

assert inputs.DB_CONFIG['host'] == 'file-host'
assert inputs.DB_CONFIG['dbname'] == 'fixture_database'
assert inputs.DB_CONFIG['port'] == 5544
assert os.environ['LLM_MODEL'] == 'fixture-model'
assert inputs.ENV_PATH.parent.name == 'submission with spaces'
assert interface.PlanNode is comparison.PlanNode is inputs.PlanNode
assert not any(name == 'src' or name.startswith(('src.', 'config.')) for name in sys.modules)

raw = [{'Plan': {'Node Type': 'Seq Scan', 'Relation Name': 'items', 'Filter': 'price > 10'}}]
ai = {'node_type': 'Seq Scan', 'properties': {'relation_name': 'items', 'condition': 'price > 20'}, 'children': []}
pg_plan, ai_plan = inputs.parse_postgres_plan(raw), inputs.parse_ai_plan(ai)
result = comparison.compare_plans(raw, ai)
assert comparison.calculate_difference_rate(result) == 100
assert result['tree_edit_distance'] == 0
view = interface.adapt_comparison_result(result, interface.index_plan(pg_plan), interface.index_plan(ai_plan))
assert view['node_statuses']['postgres']['0'] == 'changed'

project.get_postgres_plan = lambda sql: raw
project.get_database_context = lambda: 'fixture context'
def generate(sql, schema):
    assert schema == 'fixture context'
    return ai_plan
project.get_ai_plan = generate
output = project.run_query('SELECT * FROM items WHERE price > 10', display=False)
assert output['comparison_result']['difference_rate'] == 100
assert isinstance(output['postgres_plan'], inputs.PlanNode)
""")


def test_environment_overrides_file_and_unrelated_env_is_not_loaded(standalone):
    application, _, environment = standalone
    environment = {**environment, "DB_HOST": "environment-host"}
    run_isolated(standalone, "import preprocessing; assert preprocessing.DB_CONFIG['host'] == 'environment-host'", environment)
    (application / ".env").unlink()
    run_isolated(standalone, "import preprocessing; assert preprocessing.DB_CONFIG['host'] is None")


def test_four_file_entrypoint_opens_gui(standalone):
    output = run_isolated(standalone, """
from pathlib import Path
import runpy
import tkinter as tk
import interface

try:
    window = tk.Tk()
except tk.TclError as exc:
    print('NO_DISPLAY:', exc)
    sys.exit(0)
window.withdraw()
tk.Tk = lambda: window
constructed = []
original_app = interface.ComparisonApp
def checked_app(*args, **kwargs):
    app = original_app(*args, **kwargs)
    assert app.run_callback is not None and app.load_callback is not None
    app.load_demo()
    assert app.postgres_tree.nodes and app.ai_tree.nodes
    constructed.append(app)
    return app
interface.ComparisonApp = checked_app
window.after(150, window.destroy)
try:
    runpy.run_path(str(Path(sys.argv[1]) / 'project.py'), run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert len(constructed) == 1
print('GUI_OK')
""")
    if "NO_DISPLAY:" in output:
        pytest.skip(output.strip())
    assert "GUI_OK" in output
