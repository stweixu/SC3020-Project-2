# SC3020 Project 2

Framework for comparing PostgreSQL-generated and AI-generated Query Execution Plans (QEPs).

## Structure
- `project.py`: application entry point
- `preprocessing.py`: PostgreSQL and LLM plan retrieval/parsing
- `comparison.py`: QEP tree comparison
- `interface.py`: GUI and visualisation
- `config/`: shared configuration
- `data/`: dataset setup notes (do not commit generated datasets)
- `tests/`: automated tests
- `docs/`: setup, architecture and testing notes

## Setup
1. Create and activate a Python virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in local credentials.
4. Follow `docs/setup.md` for PostgreSQL and dataset setup.

The application is a scaffold; implementation is still required.
