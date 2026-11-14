# GUI and visualisation

## Framework

The interface uses Tkinter/ttk and a Tk Canvas. Tkinter is included with the
standard Python installers on Windows and macOS. Canvas supports node selection,
scrolling and zooming without a separate Graphviz executable or browser server.
ttk provides native-style form controls. PySide6 offers more advanced widgets but
adds a larger dependency; a browser UI would require an additional rendering and
launch stack. Tkinter is sufficient for this project's two-tree desktop viewer.

Check availability with `python -m tkinter`. If Tk is missing, install Python
with Tcl/Tk support and recreate the virtual environment with that interpreter.

## Starting the application

From the repository root, with the project environment active:

```sh
python src/project.py
```

`python -m src.project` also works. The default mode is **PostgreSQL only** and
does not need an API key. Database settings come from `.env`; restart the app
after editing them. The connection's database and host are shown above the editor.

1. Enter SQL and click **Fetch / compare plans** (Ctrl+Enter or Cmd+Enter).
2. Inspect the tree using the scrollbars and zoom controls. Child order is
   preserved; the root is at the top. Middle-button dragging pans a tree.
3. Click a node for its full operator name and properties. Long canvas labels
   are abbreviated, but the details panel retains the complete values.
4. For live AI retrieval, configure `LLM_API_KEY`/`LLM_MODEL`, restart, and choose
   **PostgreSQL + AI**. Use **AI schema context…** to edit or load the matching
    schema/index context. Blank context reads accessible schemas, constraints and
    indexes from the connected database automatically; no TPC-H names are assumed.
5. Select a finding to highlight its affected nodes in both trees and read the
    complete explanation. Colours have text labels as well as a legend.
6. **Open saved result…** opens a successful `pairs/*.json` file created by the
   offline evaluator. It restores SQL, trees, findings and the calculated score
   without database/API requests. A saved-result banner distinguishes it from
   live retrieval. Failed records must be inspected in the evaluation output.

PostgreSQL/API/comparison work runs on a daemon worker thread. A queue delivers
results to the Tk event loop; only the main thread updates widgets. New requests
clear old results, and controls prevent overlapping requests. Closing the window
does not wait for an outstanding request; it does not cancel the remote request.

## Demo and failure handling

**Load demo** displays synthetic trees, findings, and a fixed illustrative score.
It does not contact PostgreSQL or Gemini and does not run a comparison algorithm.
The demo banner and metric label identify it explicitly. The fixture intentionally
includes all colour statuses; it is not an executable-plan correctness example.

If AI generation fails, the retrieved PostgreSQL plan remains visible. If the
comparison function is unimplemented or fails, both retrieved plans remain visible
in neutral colours. No score is invented: **unavailable** is distinct from 0%.

For real comparison output, the adapter uses `classify_comparison()` from
`comparison.py`. It derives the score and colours together rather than trusting
a potentially stale cached percentage. The label includes the percentage and
`different units / total units`. The score counts matched pairs once, not twice
because both trees display them. Final experiments must use real generated plans.

## Comparator integration

`adapt_comparison_result()` in `src/interface.py` is the integration boundary.
It accepts Member 3's documented output (`matches`, `unmatched_postgres`,
`unmatched_ai`, `differences`, summaries) and delegates classification to the
shared metric helper. Neither the renderer nor the adapter matches nodes or
detects new differences.

- **Green — no detected change:** a matched pair with the same normalised
  operator and no scored finding affecting either member.
- **Red — changed:** a matched pair with an operator/property/structural change.
- **Blue — PostgreSQL only:** unmatched PostgreSQL nodes.
- **Purple — AI only:** unmatched AI nodes.
- **Grey — not compared:** missing/failed comparison.

`same_operator=True` alone is insufficient for green. Similarity rows link both
matched nodes, even at different paths, and explicitly acknowledge unknown
properties. Difference rows combine Member 3's description and explanation and
retain their referenced original-tree IDs. Structural findings reference the top
joins as supplied by her comparator, not every changed edge. Unknown references,
incomplete node coverage or invalid metric data leave both trees neutral with a
visible error. See [evaluation.md](evaluation.md) for the score and limitations.

Each tree is indexed independently with child paths: `0` is the root, `0.0` its
first child, `0.1` its second child, and `0.1.0` the latter's first child. The same
path in two trees does **not** imply a correspondence. Paths refer to the original
displayed trees, so a comparator that rewrites trees must provide a mapping back.

### Presentation-only contract

The synthetic demo still accepts this small presentation format. It is not the
backend comparator format:

```python
{
    "node_statuses": {
        "postgres": {"0": "unchanged", "0.0": "changed"},
        "ai": {"0": "unchanged", "0.1": "changed"},
    },
    "findings": [
        {
            "category": "Scan method",
            "postgres_node_ids": ["0.0"],
            "ai_node_ids": ["0.1"],
            "explanation": "PostgreSQL uses Seq Scan; the AI proposes Index Scan.",
        }
    ],
    "similarities": ["Both plans use a hash join."],
    "difference_rate": None,
}
```

- Status values: `unchanged`, `changed`, `postgres_only`, `ai_only`, `not_compared`.
- Missing node statuses are neutral (`not_compared`), never implicitly unchanged.
- Findings can reference multiple nodes for structural changes. A one-sided
  finding uses an empty list for the other plan.
- Similarities can be strings or node-linked entries in `findings` with category
  `Similarity`.
- `difference_rate` is `None` or a finite number from **0 to 100**, not a fraction.
- Unknown node references and invalid percentages are reported rather than
  silently displayed. Existing plan trees remain visible without comparison colours.
- The viewer accepts PostgreSQL's `Relation Name`/`Alias` and the AI's
  `relation_name`/`alias` for labels. This is display compatibility only, not
  semantic normalisation for comparison.

## Verification and remaining limits

```sh
python -m pytest tests/test_interface.py -q
```

Tests cover repeated relations, real comparator/metric integration, condition
changes, cross-path similarities, invalid references/percentages, missing
comparison data, demo selection, zoom, stale-result clearing, background
completion, and partial backend failures. Tk interaction tests require a display.

The viewer limits plans to 2,000 nodes and uses a simple leaf-based layout. Wide
trees need horizontal scrolling; it does not fold subtrees or draw shared DAG
nodes. Database selection currently uses `.env`, not an in-app connection editor.
Windows execution still needs verification on a Windows machine.

## Development acknowledgement

OpenAI GPT-6 Astra, accessed through OpenCode, assisted with the GUI implementation,
application integration, tests, and this documentation. The team should include
this usage in its final consolidated LLM acknowledgement.
