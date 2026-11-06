"""Retrieve and parse PostgreSQL and AI-generated QEPs."""

def get_postgres_plan(sql):
    raise NotImplementedError

def parse_postgres_plan(raw_plan):
    raise NotImplementedError

def get_ai_plan(sql, schema):
    raise NotImplementedError

def parse_ai_plan(raw_plan):
    raise NotImplementedError
