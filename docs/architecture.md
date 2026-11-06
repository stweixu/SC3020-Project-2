# Architecture

Planned flow:

SQL query -> preprocessing (PostgreSQL QEP + AI QEP) -> comparison -> interface

Both QEPs should be converted into an agreed common tree representation before comparison. Agree on the `PlanNode` fields and function contracts before implementation.
