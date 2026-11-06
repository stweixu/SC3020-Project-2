from src.plan_node import PlanNode

# Create a leaf node
scan = PlanNode(
    node_type="Seq Scan",
    properties={
        "relation_name": "customer",
        "estimated_rows": 150000
    }
)

# Create a parent node
join = PlanNode(
    node_type="Hash Join",
    properties={"join_type": "Inner"},
    children=[scan]
)

print(join.node_type)
print(join.children[0].node_type)
print(join.children[0].properties)