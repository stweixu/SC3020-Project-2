
from src.preprocessing import (
    get_postgres_plan,
    parse_postgres_plan
)


def test_parse_postgres_plan():
    # Simulated PostgreSQL JSON execution plan
    raw_plan = [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Join Type": "Inner",
                "Startup Cost": 100.0,
                "Total Cost": 500.0,
                "Plan Rows": 100,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "customer",
                        "Startup Cost": 0.0,
                        "Total Cost": 200.0,
                        "Plan Rows": 150000
                    },
                    {
                        "Node Type": "Hash",
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Relation Name": "orders",
                                "Plan Rows": 1500000
                            }
                        ]
                    }
                ]
            }
        }
    ]

    root = parse_postgres_plan(raw_plan)

    assert root.node_type == "Hash Join"
    assert root.properties["Join Type"] == "Inner"
    assert len(root.children) == 2

    assert root.children[0].node_type == "Seq Scan"
    assert root.children[0].properties["Relation Name"] == "customer"

    assert root.children[1].node_type == "Hash"
    assert root.children[1].children[0].node_type == "Seq Scan"


def test_get_postgres_plan():
    sql = """
        SELECT *
        FROM customer
        WHERE c_custkey = 100;
    """

    raw_plan = get_postgres_plan(sql)
    root = parse_postgres_plan(raw_plan)

    assert root.node_type is not None
    assert isinstance(root.properties, dict)
    assert isinstance(root.children, list)

    print("PostgreSQL plan:", root.node_type)
    print("Properties:", root.properties)


def test_complex_postgres_query():
    sql = """
        SELECT
            c.c_name,
            COUNT(o.o_orderkey) AS order_count,
            SUM(o.o_totalprice) AS total_spent
        FROM customer c
        JOIN orders o
            ON c.c_custkey = o.o_custkey
        WHERE c.c_mktsegment = 'BUILDING'
        GROUP BY c.c_custkey, c.c_name
        ORDER BY total_spent DESC
        LIMIT 10;
    """

    raw_plan = get_postgres_plan(sql)
    root = parse_postgres_plan(raw_plan)

    def print_tree(node, depth=0):
        print("  " * depth + node.node_type)
        for child in node.children:
            print_tree(child, depth + 1)

    print_tree(root)

    assert root.node_type is not None
    assert len(root.children) > 0