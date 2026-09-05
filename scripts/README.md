# SKOPE scripts

These files are command-line entrypoints, not reusable application modules.
Runtime API logic belongs in `backend/app`; corpus-indexing logic belongs in
`workers/ingestion`. Run scripts from the project root unless noted otherwise.

## Normal development and acceptance

- `run_api.py` starts the FastAPI development server.
- `run_tests.py` runs the regression suite with the project's local dependency
  directories available on `sys.path`.
- `run_evaluation.py` runs routing and retrieval evaluation. SQL evaluation is
  opt-in because it consumes Gemini quota.
- `validate_index.py` verifies the latest successful ingestion run and requires
  PostgreSQL and Qdrant to contain matching chunk counts.

## Database schema

- `ingest_to_db.py` rebuilds and loads the canonical PostgreSQL database from
  the harmonized source data.
- `apply_app_schema.py` applies application, RAG, evaluation, and audit tables.
- `apply_analytics_views.py` applies the curated, read-only analytics views used
  by text-to-SQL.
- `reconcile_and_validate.py` performs cross-source reconciliation checks.

## Corpus preparation and validation

- `validate_ground_truth_phase1.py`, `build_ground_truth_phase1.py`, and
  `promote_ground_truth_phase1.py` are the Phase 1 ground-truth workflow.
- `prepare_phase2_data.py`, `generate_phase2_pdfs.py`,
  `validate_phase2_data.py`, and `validate_phase2_pdfs.py` are the Phase 2
  generation workflow.
- `generate_business_emails.py` and `generate_rag_docs.py` are controlled corpus
  generators.
- `harmonize_raw_data.py` is the original harmonization utility.

The generation and promotion scripts can intentionally replace generated
artifacts or database contents. They are retained for reproducibility, but are
not part of normal API startup and should not be run merely to launch SKOPE.
