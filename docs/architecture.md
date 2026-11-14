# Architecture

Planned flow:

SQL query -> project.run_query -> preprocessing (PostgreSQL QEP + AI QEP) -> comparison -> interface

`src/project.py` orchestrates the pipeline and defaults the AI schema context
to `database/schema.sql`. Both QEPs use the common `PlanNode` representation
from `src/plan_node.py` before comparison. Plan comparison is implemented in
`src/comparison.py` (see [comparison.md](comparison.md)); the display function
is still implemented by its team member.
