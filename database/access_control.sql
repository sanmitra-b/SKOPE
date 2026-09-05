-- Database group roles. Create login roles separately and grant one of these groups.
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'skope_rag_reader') THEN
        CREATE ROLE skope_rag_reader NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'skope_pii_operator') THEN
        CREATE ROLE skope_pii_operator NOLOGIN;
    END IF;
END
$roles$;

REVOKE ALL ON SCHEMA skope FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA skope FROM PUBLIC;
GRANT USAGE ON SCHEMA skope TO skope_rag_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA skope TO skope_rag_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA skope GRANT SELECT ON TABLES TO skope_rag_reader;

REVOKE ALL ON SCHEMA restricted FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA restricted FROM PUBLIC;
GRANT USAGE ON SCHEMA restricted TO skope_pii_operator;
GRANT SELECT, UPDATE ON ALL TABLES IN SCHEMA restricted TO skope_pii_operator;
ALTER DEFAULT PRIVILEGES IN SCHEMA restricted GRANT SELECT, UPDATE ON TABLES TO skope_pii_operator;

COMMENT ON ROLE skope_rag_reader IS 'Read-only access to RAG/analytics schema; no restricted PII access';
COMMENT ON ROLE skope_pii_operator IS 'Operational access to restricted contacts; grant only to authorized backend workflows';
