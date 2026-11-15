"""Live connection smoke test using the application's configured database."""

import psycopg
from src.preprocessing import DB_CONFIG

def test_database_connection():
    with psycopg.connect(**DB_CONFIG, connect_timeout=10) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
