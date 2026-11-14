"""Desktop plan viewer and presentation adapter for comparison results."""

import json
import math
import queue
import threading
import tkinter as tk
from collections.abc import Mapping
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from src.plan_node import PlanNode
from src.comparison import classify_comparison


STATUS_STYLES = {
    "not_compared": ("Not compared", "#edf1f5"),
    "unchanged": ("No detected change", "#d7efdf"),
    "changed": ("Changed", "#ffd5d5"),
    "postgres_only": ("PostgreSQL only", "#cce4ff"),
    "ai_only": ("AI only", "#dcd6fa"),
}


def index_plan(root):
    """Index a tree by child paths: 0, 0.0, 0.1, 0.1.0, ..."""
    nodes = {}
    if root is None:
        return nodes
    stack = [("0", root)]
    seen = set()
    while stack:
        path, node = stack.pop()
        if not isinstance(node, PlanNode):
            raise ValueError("Plans must contain PlanNode objects.")
        if id(node) in seen:
            raise ValueError("A plan must be a tree, without cycles or shared nodes.")
        if len(nodes) >= 2000:
            raise ValueError("The viewer supports up to 2,000 nodes per plan.")
        if not isinstance(node.node_type, str) or not isinstance(node.properties, Mapping):
            raise ValueError("Each node needs an operator name and a property mapping.")
        if not isinstance(node.children, (list, tuple)):
            raise ValueError("Node children must be a list or tuple.")
        seen.add(id(node))
        nodes[path] = node
        stack.extend(
            (f"{path}.{i}", child)
            for i, child in reversed(list(enumerate(node.children)))
        )
    return nodes


def _comparison_view(result, postgres_ids, ai_ids):
    """Translate the comparator contract using the metric's classifications."""
    metric = classify_comparison(result)
    for side, ids in (("postgres", postgres_ids), ("ai", ai_ids)):
        if set(metric["node_statuses"][side]) != set(ids):
            raise ValueError(f"Comparison does not classify every displayed {side} node.")
    findings = []
    for difference in result["differences"]:
        if any(not isinstance(difference.get(key), str) for key in ("description", "explanation")):
            raise ValueError("Each difference needs a description and explanation.")
        findings.append({
            "category": difference["type"].replace("_", " ").title(),
            "explanation": difference["description"] + "\n\n" + difference["explanation"],
            **{f"{side}_node_ids": [difference[f"{side}_node_id"]]
               if difference.get(f"{side}_node_id") is not None else []
               for side in ("postgres", "ai")},
        })
    for match in result["matches"]:
        pg_id, ai_id = match["postgres"]["node_id"], match["ai"]["node_id"]
        if metric["node_statuses"]["postgres"][pg_id] == "unchanged":
            findings.append({
                "category": "Similarity",
                "explanation": f"No difference detected for {match['postgres']['label']} "
                               f"and {match['ai']['label']} under the comparison rules. "
                               "Properties omitted by either plan may not have been compared.",
                "postgres_node_ids": [pg_id], "ai_node_ids": [ai_id],
            })
    return {"node_statuses": metric["node_statuses"], "findings": findings,
            "difference_rate": metric["difference_rate"], "metric": metric}


def adapt_comparison_result(result, postgres_ids, ai_ids):
    """Accept Member 3's result or the presentation-only demo contract.

    Missing statuses remain 'not compared'; paths do not imply node matches.
    """
    if result is None:
        result = {}
    if not isinstance(result, Mapping):
        raise ValueError("Comparison output must be a mapping; update the UI adapter.")
    if "matches" in result:
        return _comparison_view(result, postgres_ids, ai_ids)
    allowed = {"node_statuses", "findings", "similarities", "difference_rate"}
    if result and not allowed.intersection(result):
        raise ValueError("Unrecognised comparison format; update adapt_comparison_result().")
    statuses = result.get("node_statuses", {})
    if not isinstance(statuses, Mapping):
        raise ValueError("node_statuses must be a mapping.")
    ids = {"postgres": set(postgres_ids), "ai": set(ai_ids)}
    normalised = {"node_statuses": {}, "findings": [], "difference_rate": None}
    for side in ids:
        values = statuses.get(side, {})
        if not isinstance(values, Mapping):
            raise ValueError(f"Node statuses for {side} must be a mapping.")
        for node_id, status in values.items():
            if (not isinstance(node_id, str) or node_id not in ids[side]
                    or not isinstance(status, str) or status not in STATUS_STYLES):
                raise ValueError(f"Invalid {side} node status: {node_id!r}, {status!r}.")
            if (side == "postgres" and status == "ai_only") or (
                side == "ai" and status == "postgres_only"
            ):
                raise ValueError(f"Status {status!r} belongs to the other plan.")
        normalised["node_statuses"][side] = dict(values)
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("findings must be a list.")
    for finding in findings:
        if not isinstance(finding, Mapping) or not isinstance(finding.get("explanation"), str):
            raise ValueError("Each finding needs a text explanation.")
        item = {"category": str(finding.get("category", "Difference")),
                "explanation": finding["explanation"]}
        for side in ids:
            key = f"{side}_node_ids"
            refs = finding.get(key, [])
            if not isinstance(refs, list) or any(
                not isinstance(ref, str) or ref not in ids[side] for ref in refs
            ):
                raise ValueError(f"A finding references an unknown {side} node.")
            item[key] = list(refs)
        normalised["findings"].append(item)
    similarities = result.get("similarities", [])
    if not isinstance(similarities, list) or any(not isinstance(s, str) for s in similarities):
        raise ValueError("similarities must be a list of strings.")
    for text in similarities:
        normalised["findings"].append({
            "category": "Similarity", "explanation": text,
            "postgres_node_ids": [], "ai_node_ids": [],
        })
    rate = result.get("difference_rate")
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
            raise ValueError("difference_rate must be a finite percentage or None.")
        if not 0 <= rate <= 100:
            raise ValueError("difference_rate must be between 0 and 100.")
        normalised["difference_rate"] = float(rate)
    return normalised


class PlanTree(ttk.Frame):
    """Scrollable, zoomable tree canvas with selectable nodes."""

    def __init__(self, parent, title, on_select):
        super().__init__(parent, padding=6)
        self.nodes = {}
        self.statuses = {}
        self.positions = {}
        self.rectangles = {}
        self.selected = set()
        self.zoom = 1.0
        self.on_select = on_select
        self.message = "No plan loaded."
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(toolbar, text=title, font=("TkDefaultFont", 12, "bold")).pack(side="left")
        for label, factor in (("−", 0.8), ("+", 1.25)):
            ttk.Button(toolbar, text=label, width=3,
                       command=lambda f=factor: self.set_zoom(self.zoom * f)).pack(side="right")
        ttk.Button(toolbar, text="Reset", command=lambda: self.set_zoom(1.0)).pack(side="right")
        self.canvas = tk.Canvas(self, background="#fafbfd", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.bind("<ButtonPress-2>", lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind("<B2-Motion>", lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

    def show(self, root, statuses=None, message="No plan available."):
        self.nodes = index_plan(root)
        self.statuses = statuses or {}
        self.message = message
        self.selected = set()
        self.zoom = 1.0
        self.positions = {}
        leaf = 0
        # Reverse preorder places children before their parents without recursion.
        for path, node in reversed(list(self.nodes.items())):
            if node.children:
                xs = [self.positions[f"{path}.{i}"][0] for i in range(len(node.children))]
                x = (xs[0] + xs[-1]) / 2
            else:
                x = leaf * 250 + 135
                leaf += 1
            self.positions[path] = (x, path.count(".") * 125 + 65)
        # Reverse traversal visits right-hand leaves first; reflect to preserve input order.
        width = max(leaf, 1) * 250 + 20
        self.positions = {path: (width - x, y) for path, (x, y) in self.positions.items()}
        self.draw()
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
        if self.nodes:
            self._reveal("0")

    def set_zoom(self, value):
        self.zoom = max(0.35, min(1.8, value))
        self.draw()
        if self.nodes:
            self._reveal(next(iter(self.selected), "0"))

    def draw(self):
        self.canvas.delete("all")
        self.rectangles = {}
        if not self.nodes:
            self.canvas.create_text(24, 35, anchor="nw", text=self.message,
                                    width=360, fill="#526172")
            self.canvas.configure(scrollregion=(0, 0, 400, 150))
            return
        z = self.zoom
        for path, node in self.nodes.items():
            x, y = self.positions[path]
            for i in range(len(node.children)):
                cx, cy = self.positions[f"{path}.{i}"]
                self.canvas.create_line(x*z, (y+43)*z, cx*z, (cy-43)*z,
                                        fill="#8492a3", width=2, arrow=tk.LAST)
        for path, node in self.nodes.items():
            x, y = self.positions[path]
            status = self.statuses.get(path, "not_compared")
            label, colour = STATUS_STYLES[status]
            tag = f"node:{path}"
            selected = path in self.selected
            rectangle = self.canvas.create_rectangle(
                (x-110)*z, (y-43)*z, (x+110)*z, (y+43)*z, fill=colour,
                outline="#185abc" if selected else "#758397",
                width=3 if selected else 1, tags=(tag,),
            )
            self.rectangles[path] = rectangle
            relation = node.properties.get("Relation Name") or node.properties.get("relation_name")
            alias = node.properties.get("Alias") or node.properties.get("alias")
            detail = str(relation or "")
            if alias and alias != relation:
                detail += f" ({alias})"
            operator_label = node.node_type if len(node.node_type) <= 29 else node.node_type[:26] + "…"
            detail_label = detail if len(detail) <= 29 else detail[:26] + "…"
            text = f"{operator_label}\n{detail_label or '—'}\n{label}"
            self.canvas.create_text(x*z, y*z, text=text, width=205*z,
                                    font=("TkDefaultFont", max(7, round(10*z))),
                                    fill="#172b40", tags=(tag,))
            self.canvas.tag_bind(tag, "<Button-1>", lambda event, p=path: self.on_select(p))
        bounds = self.canvas.bbox("all")
        self.canvas.configure(scrollregion=(0, 0, bounds[2]+25, bounds[3]+25))

    def highlight(self, paths):
        self.selected = set(paths)
        for path, item in self.rectangles.items():
            active = path in self.selected
            self.canvas.itemconfigure(item, outline="#185abc" if active else "#758397",
                                      width=3 if active else 1)
        if paths:
            self._reveal(paths[0])

    def _reveal(self, path):
        x, y = self.positions[path]
        bounds = self.canvas.bbox("all")
        width, height = bounds[2]+25, bounds[3]+25
        self.canvas.xview_moveto(max(0, (x*self.zoom-self.canvas.winfo_width()/2)/width))
        self.canvas.yview_moveto(max(0, (y*self.zoom-self.canvas.winfo_height()/2)/height))


class ComparisonApp:
    """Application window; background workers never access Tk widgets."""

    def __init__(self, root, run_callback=None, schema="", connection_label="", load_callback=None):
        self.root = root
        self.run_callback = run_callback
        self.load_callback = load_callback
        self.schema = schema
        self.results = queue.Queue()
        self.busy = False
        self.poll_id = None
        self.findings = []
        self.root.title("QEP Comparison")
        width = min(1280, self.root.winfo_screenwidth() - 60)
        height = min(900, self.root.winfo_screenheight() - 100)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(900, width), min(650, height))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="Ready. Fetch a PostgreSQL plan or load the labelled demo.")
        self.rate = tk.StringVar(value="Difference rate: unavailable")
        self.mode = tk.StringVar(value="PostgreSQL only")
        self._build(connection_label)

    def _build(self, connection_label):
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Query execution plan comparison",
                  font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(container, text=connection_label or "Demo viewer — no database connection required").pack(anchor="w", pady=(3, 8))
        query_frame = ttk.LabelFrame(container, text="SQL query", padding=8)
        query_frame.pack(fill="x")
        self.sql = ScrolledText(query_frame, height=5, wrap="word", undo=True,
                                font="TkFixedFont")
        self.sql.pack(fill="x")
        self.sql.bind("<Control-Return>", lambda event: self.run())
        self.sql.bind("<Command-Return>", lambda event: self.run())
        controls = ttk.Frame(query_frame)
        controls.pack(fill="x", pady=(8, 0))
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode, state="readonly",
                                    values=("PostgreSQL only", "PostgreSQL + AI"), width=23)
        self.mode_box.pack(side="left")
        self.run_button = ttk.Button(controls, text="Fetch / compare plans", command=self.run)
        self.run_button.pack(side="left", padx=8)
        if self.run_callback is None:
            self.run_button.state(["disabled"])
        self.demo_button = ttk.Button(controls, text="Load demo", command=self.load_demo)
        self.demo_button.pack(side="left")
        self.open_button = ttk.Button(controls, text="Open saved result…", command=self.open_saved)
        self.open_button.pack(side="left", padx=8)
        if self.load_callback is None:
            self.open_button.state(["disabled"])
        self.schema_button = ttk.Button(controls, text="AI schema context…", command=self.edit_schema)
        self.schema_button.pack(side="right")
        self.progress = ttk.Progressbar(container, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 4))
        ttk.Label(container, textvariable=self.status, wraplength=1100).pack(anchor="w")
        ttk.Label(container, textvariable=self.rate, font=("TkDefaultFont", 12, "bold")).pack(anchor="w", pady=6)
        legend = ttk.Frame(container)
        legend.pack(fill="x", pady=(0, 6))
        for label, colour in STATUS_STYLES.values():
            tk.Label(legend, text=f"  {label}  ", background=colour,
                     foreground="#172b40").pack(side="left", padx=(0, 6))
        panes = ttk.Panedwindow(container, orient="vertical")
        panes.pack(fill="both", expand=True)
        trees = ttk.Panedwindow(panes, orient="horizontal")
        self.postgres_tree = PlanTree(trees, "PostgreSQL plan", lambda p: self.select_node("postgres", p))
        self.ai_tree = PlanTree(trees, "AI plan", lambda p: self.select_node("ai", p))
        trees.add(self.postgres_tree, weight=1)
        trees.add(self.ai_tree, weight=1)
        panes.add(trees, weight=3)
        bottom = ttk.Panedwindow(panes, orient="horizontal")
        findings_frame = ttk.LabelFrame(bottom, text="Differences and similarities", padding=6)
        self.finding_list = ttk.Treeview(findings_frame, columns=("category", "explanation"),
                                         show="headings", height=6, selectmode="browse")
        for column, title, width in (("category", "Category", 120), ("explanation", "Explanation", 380)):
            self.finding_list.heading(column, text=title)
            self.finding_list.column(column, width=width, minwidth=80)
        self.finding_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(findings_frame, orient="vertical", command=self.finding_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.finding_list.configure(yscrollcommand=scrollbar.set)
        findings_frame.rowconfigure(0, weight=1)
        findings_frame.columnconfigure(0, weight=1)
        self.finding_list.bind("<<TreeviewSelect>>", self.select_finding)
        details_frame = ttk.LabelFrame(bottom, text="Selected node / full explanation", padding=6)
        self.details = ScrolledText(details_frame, height=7, wrap="word", state="disabled")
        self.details.pack(fill="both", expand=True)
        bottom.add(findings_frame, weight=1)
        bottom.add(details_frame, weight=1)
        panes.add(bottom, weight=1)
        self.postgres_tree.show(None)
        self.ai_tree.show(None)

    def _details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _set_busy(self, busy):
        self.busy = busy
        for button in (self.run_button, self.demo_button, self.schema_button, self.open_button):
            button.state(["disabled"] if busy else ["!disabled"])
        if self.load_callback is None:
            self.open_button.state(["disabled"])
        if self.run_callback is None:
            self.run_button.state(["disabled"])
        self.mode_box.configure(state="disabled" if busy else "readonly")
        self.sql.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def run(self):
        if self.busy or self.run_callback is None:
            return
        sql = self.sql.get("1.0", "end").strip()
        if not sql:
            self.status.set("Enter a SQL query first.")
            return
        mode = "postgres" if self.mode.get() == "PostgreSQL only" else "both"
        self.show_result(None, None, None, "Retrieving plans…")
        self._set_busy(True)
        schema = self.schema

        def work():
            try:
                result = self.run_callback(sql, schema, mode)
                self.results.put((result, None))
            except Exception as exc:
                self.results.put((None, str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.poll_id = self.root.after(100, self._poll)

    def _poll(self):
        self.poll_id = None
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            self.poll_id = self.root.after(100, self._poll)
            return
        self._set_busy(False)
        if error:
            self.status.set("Request failed. See details below.")
            self._details(error)
            return
        try:
            self.show_result(result["postgres_plan"], result.get("ai_plan"),
                             result.get("comparison_result"), result.get("notice", "Plans loaded."))
        except (ValueError, KeyError, TypeError) as exc:
            self.status.set("Could not display the result.")
            self._details(str(exc))

    def show_result(self, postgres_plan, ai_plan, comparison_result, notice="Plans loaded."):
        # Render neutral trees first so invalid comparison metadata cannot hide the plans.
        self.postgres_tree.show(postgres_plan)
        self.ai_tree.show(ai_plan, message="No AI plan loaded. Use AI mode or Load demo.")
        self.findings = []
        self.finding_list.delete(*self.finding_list.get_children())
        self.rate.set("Difference rate: unavailable")
        self._details("Select a node to inspect its properties, or a finding to highlight affected nodes.")
        try:
            view = adapt_comparison_result(comparison_result, self.postgres_tree.nodes, self.ai_tree.nodes)
        except (ValueError, KeyError, TypeError) as exc:
            self.status.set("Plans loaded; comparison output needs attention.")
            self._details(str(exc))
            return
        for side, tree in (("postgres", self.postgres_tree), ("ai", self.ai_tree)):
            tree.statuses = view["node_statuses"][side]
            tree.draw()
        self.findings = view["findings"]
        for i, item in enumerate(self.findings):
            self.finding_list.insert("", "end", iid=str(i), values=(item["category"], item["explanation"]))
        rate = view["difference_rate"]
        if rate is not None:
            self.rate.set(f"Difference rate: {rate:.1f}%")
            if "metric" in view:
                metric = view["metric"]
                self.rate.set(f"Difference rate: {rate:.1f}% — "
                              f"{metric['different_units']} / {metric['total_units']} comparison units differ")
        if len(notice) > 240:
            self.status.set(notice[:237] + "… (see details)")
            self._details(notice)
        else:
            self.status.set(notice)

    def select_node(self, side, path):
        tree = self.postgres_tree if side == "postgres" else self.ai_tree
        other = self.ai_tree if side == "postgres" else self.postgres_tree
        tree.highlight([path])
        other.highlight([])
        node = tree.nodes[path]
        status = STATUS_STYLES[tree.statuses.get(path, "not_compared")][0]
        text = f"{side.upper()} node {path}\n{node.node_type} — {status}\n\n"
        text += json.dumps(dict(node.properties), indent=2, ensure_ascii=False, default=str)
        related = [f["explanation"] for f in self.findings if path in f[f"{side}_node_ids"]]
        if related:
            text += "\n\nFindings:\n" + "\n\n".join(related)
        self._details(text)

    def select_finding(self, event=None):
        selection = self.finding_list.selection()
        if not selection:
            return
        finding = self.findings[int(selection[0])]
        self.postgres_tree.highlight(finding["postgres_node_ids"])
        self.ai_tree.highlight(finding["ai_node_ids"])
        self._details(f"{finding['category']}\n\n{finding['explanation']}")

    def open_saved(self):
        if self.busy or self.load_callback is None:
            return
        filename = filedialog.askopenfilename(parent=self.root, title="Open an evaluated pair",
                                             filetypes=[("Evaluation pair", "*.json")])
        if not filename:
            return
        try:
            result = self.load_callback(filename)
            self.show_result(result["postgres_plan"], result["ai_plan"],
                             result["comparison_result"], result["notice"])
            self.sql.delete("1.0", "end")
            self.sql.insert("1.0", result["sql"])
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            self.show_result(None, None, None, "Could not open saved result.")
            self._details(str(exc))

    def edit_schema(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Database context for the AI")
        dialog.geometry("760x500")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Supply tables, columns, types and indexes for the connected database.\n"
                  "Leave blank to read accessible schemas and indexes automatically on each request.\n"
                  "This context is sent to the AI; it does not change the database connection.",
                  padding=10).pack(anchor="w")
        editor = ScrolledText(dialog, wrap="word", font="TkFixedFont")
        editor.pack(fill="both", expand=True, padx=10)
        editor.insert("1.0", self.schema)
        message = tk.StringVar()
        ttk.Label(dialog, textvariable=message).pack(anchor="w", padx=10)

        def load_file():
            filename = filedialog.askopenfilename(parent=dialog, filetypes=[("Context files", "*.sql *.txt *.json"), ("All files", "*")])
            if filename:
                try:
                    content = Path(filename).read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    message.set(str(exc))
                    return
                editor.delete("1.0", "end")
                editor.insert("1.0", content)

        def apply():
            self.schema = editor.get("1.0", "end").strip()
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Load context file…", command=load_file).pack(side="left")
        ttk.Button(buttons, text="Use context", command=apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)

    def load_demo(self):
        if self.busy:
            return
        pg, ai, comparison = demo_comparison()
        self.sql.delete("1.0", "end")
        self.sql.insert("1.0", "-- Synthetic demo only; these plans are not generated from SQL.\n"
                        "-- Enter your own SQL before fetching live plans.")
        self.show_result(pg, ai, comparison,
                         "DEMO — synthetic trees and findings. The illustrative score is not an experimental result.")
        self.rate.set("Demo difference rate: 60.0% (illustrative)")

    def close(self):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
        self.root.destroy()


def demo_comparison():
    """Small synthetic fixture covering all comparison colours."""
    postgres = PlanNode("Hash Join", children=[
        PlanNode("Seq Scan", {"Relation Name": "orders"}),
        PlanNode("Hash", children=[PlanNode("Seq Scan", {"Relation Name": "customer"})]),
    ])
    ai = PlanNode("Hash Join", children=[
        PlanNode("Index Scan", {"relation_name": "orders", "index_name": "demo_orders_idx"}),
        PlanNode("Sort", children=[PlanNode("Seq Scan", {"relation_name": "customer"})]),
    ])
    result = {
        "node_statuses": {
            "postgres": {"0": "unchanged", "0.0": "changed", "0.1": "postgres_only", "0.1.0": "unchanged"},
            "ai": {"0": "unchanged", "0.0": "changed", "0.1": "ai_only", "0.1.0": "unchanged"},
        },
        "findings": [
            {"category": "Scan method", "postgres_node_ids": ["0.0"], "ai_node_ids": ["0.0"],
             "explanation": "The demo PostgreSQL tree uses Seq Scan on orders; the demo AI tree uses Index Scan. The index name is fictional."},
            {"category": "PostgreSQL only", "postgres_node_ids": ["0.1"], "ai_node_ids": [],
             "explanation": "A Hash operation appears only in the demo PostgreSQL tree."},
            {"category": "AI only", "postgres_node_ids": [], "ai_node_ids": ["0.1"],
             "explanation": "A Sort operation appears only in the demo AI tree. This fixture demonstrates rendering, not plan validity."},
        ],
        "similarities": ["Both demo trees contain Hash Join and Seq Scan on customer."],
        "difference_rate": 60.0,
    }
    return postgres, ai, result


def display_comparison(postgres_plan, ai_plan, comparison_result, *, notice="Plans loaded."):
    """Open a standalone viewer for already-generated plans."""
    root = tk.Tk()
    app = ComparisonApp(root)
    app.show_result(postgres_plan, ai_plan, comparison_result, notice)
    root.mainloop()
