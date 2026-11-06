
import psycopg

conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="sc3020_project2",
    user="sc3020_project2"
)

print("Successfully connected to PostgreSQL!")

conn.close()
