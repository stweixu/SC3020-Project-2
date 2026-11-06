
import psycopg
from pathlib import Path
import sys

# Location of the generated TPC-H .tbl files
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

TABLES = [
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
]

sys.path.insert(0, str(PROJECT_ROOT))

from config.config import DB_CONFIG

for table in TABLES:
    file_path = DATA_DIR / f"{table}.tbl"
    if not file_path.exists():
        raise FileNotFoundError(f"Missing dataset file: {file_path}")
    
conn = psycopg.connect(**DB_CONFIG)

try:
    with conn.cursor() as cur:
        for table in TABLES:
            file_path = DATA_DIR / f"{table}.tbl"

            print(f"Loading {table}...")

            with open(file_path, "r") as f:
                with cur.copy(
                    f"COPY {table} FROM STDIN WITH (FORMAT text, DELIMITER '|')"
                ) as copy:
                    for line in f:
                        # TPC-H .tbl files end each row with an extra |
                        if line.endswith("|\n"):
                            line = line[:-2] + "\n"
                        elif line.endswith("|"):
                            line = line[:-1]

                        copy.write(line)

            print(f"Loaded {table} successfully.")

    conn.commit()
    print("\nAll TPC-H tables loaded successfully!")

except Exception:
    conn.rollback()
    raise

finally:
    conn.close()
