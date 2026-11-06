# SC3020 Project 2

Compare PostgreSQL query execution plans (QEPs) with AI-generated plans.

## Requirements

- Python
- PostgreSQL 17
- Git LFS
- TPC-H SF1 dataset

## Setup

1. Clone the repository and download the LFS files:

   ```bash
   git lfs install
   git lfs pull
   ```

2. Create a PostgreSQL database named `sc3020_project2`.

3. Create the tables by running `database/schema.sql` in the database.

4. Configure your database connection in `.env` using `.env.example` as a template.

5. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

6. Load the dataset:

   ```bash
   python database/load_tpch.py
   ```

7. Update PostgreSQL statistics:

   ```sql
   ANALYZE;
   ```

## Dataset

- Dataset: TPC-H SF1
- Database: PostgreSQL 17
- Tables: 8
- Indexes: Primary-key indexes only
- Foreign keys: Defined in `database/schema.sql`

The `data/` directory contains the eight TPC-H SF1 `.tbl` files, tracked with Git LFS.

## Project Structure

```text
config/       Database configuration
database/     Schema and dataset loader
data/         TPC-H SF1 data
src/          Project source code
tests/        Tests
docs/         Project documentation
```
