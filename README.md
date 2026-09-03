# SchemaGuard

SchemaGuard is a Python MVP for reviewing and deploying candidate data transformations. It evaluates candidate output using schema and data-quality checks, ranks the candidates, supports a human approval decision, and can deploy an approved transformation with a snapshot and rollback path.

The current demo transforms rows from `public.customers` into `warehouse.customers` by mapping `full_name` to `name`.

## What It Does

The integrated flow is:

1. Load candidate records from a JSON file.
2. Build validation reports from the supplied sandbox result, schema flag, and output rows.
3. Rank candidates by effective confidence.
4. Create a review state for selection, rejection, or approval.
5. If a candidate is approved, snapshot the current transformation file.
6. Write the candidate code to `src/pipeline/transformations.py` and run the ETL.
7. Attempt to restore the latest snapshot if deployment ETL fails.

This repository is an MVP. The integration entry point currently consumes precomputed validation artifacts from the candidate JSON; it does not execute the sandbox or query the live database schema as part of candidate evaluation.

## Repository Layout

```text
.
├── main.py                         # CLI entry point
├── check_db.py                     # Simple database connectivity/data check
├── docker-compose.yml              # PostgreSQL 16 service
├── data/
│   ├── seed/01_init.sql            # Source and warehouse tables plus 500 rows
│   ├── candidates/mock.json        # Placeholder file; not a candidate JSON fixture
│   └── schema_snapshots/           # Timestamped transformation snapshots
└── src/
    ├── common/                     # Configuration and shared dataclasses
    ├── integration/                # Candidate loading and end-to-end orchestration
    ├── pipeline/                   # ETL and active transformation
    ├── sandbox/                    # Restricted AST-based candidate executor
    ├── validator/                  # Schema, quality, performance, ranking, reports
    ├── review/                     # Review state API and Streamlit dashboard
    └── deploy/                     # Snapshot, deployment, and rollback operations
```

## Requirements

- Python 3.10 or newer is recommended because the code uses modern type annotation syntax.
- Docker Desktop or another Docker Compose implementation.
- PostgreSQL is supplied by Docker Compose for the demo.

Install the Python dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Start the Demo Database

Start PostgreSQL from the repository root:

```bash
docker compose up -d db
```

The container is published at `localhost:5433` with these demo credentials:

| Setting | Value |
| --- | --- |
| Database | `schemaguard` |
| User | `schemaguard` |
| Password | `schemaguard` |
| Host port | `5433` |

The initialization script creates:

- `public.customers(id, full_name, email)` with 500 generated source rows.
- `warehouse.customers(id, name, email)` as the ETL target.

Check the seeded source table:

```bash
python check_db.py
```

The expected result is `500`. Initialization scripts only run when the database volume is created. To recreate the demo database from the seed script, use `docker compose down -v` and then start the service again.

## Run the CLI

Run the review flow without approving a candidate:

```bash
python main.py --candidates src/integration/tests/mock_candidates.json
```

The included fixture contains:

- `candidate-valid`, which produces `id`, `name`, and `email` and passes the data-quality checks.
- `candidate-invalid`, which omits `name` and is ranked below the valid candidate.

Approve and deploy the valid candidate:

```bash
python main.py \
  --candidates src/integration/tests/mock_candidates.json \
  --approve candidate-valid
```

An approved deployment snapshots the current `src/pipeline/transformations.py` under `data/schema_snapshots/`, writes the candidate code, and runs:

```bash
python -m src.pipeline.etl_pipeline
```

The ETL reads all rows from `public.customers`, replaces the contents of `warehouse.customers`, and inserts the transformed `id`, `name`, and `email` values.

## Candidate JSON Contract

The CLI accepts either a top-level JSON list or an object containing a `candidates` list. Each record must contain a candidate, a sandbox result, and output rows. `schema_ok` is optional and defaults to `false`.

```json
[
  {
    "candidate": {
      "id": "candidate-valid",
      "code": "def transform(row):\n    return {'id': row['id'], 'name': row['full_name'], 'email': row['email']}\n",
      "explanation": "Map full_name to the warehouse name column"
    },
    "sandbox_result": {
      "candidate_id": "candidate-valid",
      "ran_successfully": true,
      "row_count_before": 2,
      "row_count_after": 2,
      "execution_time_ms": 1.25
    },
    "schema_ok": true,
    "output_rows": [
      {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"}
    ]
  }
]
```

The candidate code must define a `transform(row)` function when it is executed through the sandbox module. The sandbox uses an AST allowlist and a small set of permitted built-ins, but it should not be treated as a complete security boundary for hostile code without process or container isolation.

## Streamlit Review Dashboard

Start the dashboard with:

```bash
streamlit run src/review/dashboard.py
```

By default it loads `src/integration/tests/mock_candidates.json`. Supply another fixture through the sidebar, or configure the default path with:

```bash
export SCHEMAGUARD_CANDIDATES_PATH=/path/to/candidates.json
streamlit run src/review/dashboard.py
```

The dashboard displays rank, confidence, schema and data-quality status, sandbox metrics, explanations, and candidate code. Its approval controls currently update Streamlit session state only; they do not invoke deployment.

## Tests

Run the focused unit test modules with Python's built-in unittest runner:

```bash
python -m unittest src.sandbox.tests.test_executor
python -m unittest src.validator.tests.test_data_quality
python -m unittest src.review.tests.test_review
python -m unittest src.integration.tests.test_integration
python -m unittest src.deploy.tests.test_deploy
```

Run the full discoverable test suite:

```bash
python -m unittest discover -s src -p 'test*.py'
```

Keep PostgreSQL running for the full suite. `src/validator/test_schema_diff.py` connects to the database during module import, even though most tests use in-memory fixtures.

## Configuration

The demo database URL is currently hardcoded in `src/common/config.py`:

```text
postgresql://schemaguard:schemaguard@localhost:5433/schemaguard
```

The only application environment variable currently read by the code is `SCHEMAGUARD_CANDIDATES_PATH`, used by the dashboard. The credentials in Docker Compose and the configuration module are demo values and should be replaced with secret-managed configuration before production use.

## Current Scope and Limitations

- Candidate validation in `run_integration()` trusts the `sandbox_result`, `schema_ok`, and `output_rows` values supplied in the input JSON.
- The live schema comparison utilities are separate from the integration flow.
- Confidence is currently binary (`1.0` or `0.0`); performance metrics are reported but do not affect ranking.
- Deployment replaces the active transformation file and the ETL deletes all rows in `warehouse.customers` before loading replacements.
- Rollback restores the latest syntactically valid snapshot and reruns ETL, but rollback can also fail if the database or ETL is unavailable.
- `data/candidates/mock.json` is not a valid candidate fixture and should not be passed to `main.py`.
- Dependency versions are not pinned and the project does not yet include packaging metadata or migration tooling.

## Development Notes

The main extension points are:

- `src/sandbox/executor.py` for executing and restricting candidate transformations.
- `src/validator/` for validation and ranking policy.
- `src/review/review_api.py` for approval rules and immutable review state.
- `src/deploy/` for snapshot, deployment, and rollback behavior.
- `src/integration/pipeline.py` for connecting those components into a single flow.

Changes to deployment or validation should be covered with the relevant focused test module before running the full suite.
