# AI-generated QEP integration

## What is implemented

Member 2's functions live in `src/preprocessing.py`:

- `get_ai_plan(sql, schema)` submits a prompt to Gemini and returns a
  `PlanNode` tree.
- `parse_ai_plan(raw_plan)` parses and validates JSON from the provider, then
  converts nested nodes into the project's existing `PlanNode` class.
- The node format follows `src/plan_node.py`: `node_type`, `properties`, and
  `children`.

The repository uses TPC-H SF1 on PostgreSQL 17. The schema is defined in
`database/schema.sql`: eight tables (`region`, `nation`, `supplier`,
`customer`, `part`, `partsupp`, `orders`, and `lineitem`), with primary-key
indexes and foreign-key relationships. Pass this DDL (or a more concise
structured summary of its tables, columns, types, and indexes) as `schema`.
There are no secondary indexes in the current schema. If that changes, update
the context passed to `get_ai_plan` so the model can consider them.

## Local setup

From the repository root:

```sh
python -m pip install -r requirements.txt
cp .env.example .env
```

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/)
and set it as `LLM_API_KEY` in `.env`. `LLM_MODEL` defaults to
`gemini-3.8-flash`. The `.env` file is ignored by Git; do not commit or share
your personal key. Gemini app or education access does not itself guarantee
Gemini API quota or billing access.

Requests time out after 30 seconds by default and make at most two attempts.
Adjust `LLM_TIMEOUT_MS` and `LLM_RETRY_ATTEMPTS` in `.env` if needed.

## Example call

```python
from pathlib import Path

from src.preprocessing import get_ai_plan

schema = Path("database/schema.sql").read_text(encoding="utf-8")
query = "SELECT c_name FROM customer WHERE c_mktsegment = 'BUILDING';"
ai_plan = get_ai_plan(query, schema)
print(ai_plan.node_type)
print(ai_plan.properties)
```

## Prompt and validation

The prompt supplies the SQL and schema context. Gemini's JSON response mode
keeps the reply in JSON form; `parse_ai_plan` then checks node types, property
objects, child arrays, and tree size before constructing `PlanNode` instances.
The model's output is a proposed plan, not PostgreSQL's actual plan. It is not
executed, and model-generated row/cost estimates are not based on live
statistics. A schema-valid JSON tree can still contain incorrect operators,
relations, conditions, or estimates.

## Selection rationale for the report

Gemini was selected because the Gemini API supports JSON output mode and has a
Python SDK, which fit the project's need to produce and parse a tree-shaped
response. OpenAI, Anthropic, and Mistral also provide structured-output
features, so Gemini is a practical implementation choice rather than the only
technically suitable provider. The application validates returned structure
and treats plan semantics as a limitation of LLM-generated estimates.

## Integration note

`get_ai_plan(sql, schema)` expects the caller to supply schema context.
`src/project.py` now loads `database/schema.sql` by default and passes it to the
AI function, then sends both plans to comparison and the result to the display
module. End-to-end execution still depends on Member 1's PostgreSQL retrieval,
Member 3's comparison, and Member 4's display functions being implemented.
