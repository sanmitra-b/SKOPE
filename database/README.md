# SKOPE PostgreSQL database

`skope_db` is the runtime database. `RAG Project Dataset/harmonized_data` remains the
Phase 1 file-level ground truth; PostgreSQL is a reproducible materialization of it.

## Boundaries

- `skope`: RAG/analytics-safe canonical tables, email metadata, provenance, and empty
  Phase 2-ready entities.
- `restricted`: operational customer contact data. These rows are retained for
  authorized workflows but are excluded from embeddings and the normal SQL agent.
- Phase 2 tables exist but remain empty until the generated documents are approved.
- Container records are not linked to orders without a source-backed relationship.
- DataCo order facts use `Order Item Id` as their source line grain.

## Rebuild

From the repository root, with Docker Desktop running:

```powershell
docker compose up -d postgres
python scripts\ingest_to_db.py --rebuild
python scripts\reconcile_and_validate.py
```

Activate the `SKOPE` Conda environment before running these commands. Install
`database/requirements-db.txt` when preparing a new environment.

Do not use `docker compose down -v` merely to rebuild PostgreSQL: that command also
removes the Qdrant index and local model-cache volumes. The ETL's explicit `--rebuild` resets only SKOPE's
database schemas and known legacy tables.

## Access roles

- `skope_rag_reader`: `SELECT` on `skope`, no access to `restricted`.
- `skope_pii_operator`: operational `SELECT`/`UPDATE` on `restricted`.
- `skope_admin`: schema/ETL owner; do not use it as the application SQL-agent login.

Create application login roles later, keep their secrets outside source control, and
grant the appropriate group role. Firebase SSO authenticates the user at the API; the
backend still needs these database permissions for authorization and containment.
