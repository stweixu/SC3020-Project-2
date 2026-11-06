class PlanNode:
    def __init__(self, node_type, properties=None, children=None):
        self.node_type = node_type
        self.properties = properties or {}
        self.children = children or []