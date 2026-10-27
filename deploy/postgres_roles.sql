-- Least-privilege database roles. Run as the postgres superuser AFTER `dsoreg.cli init-db`.
--
--   dsoreg_migrate  owns the tables; used only to create/alter the schema. Never used by the app.
--   dsoreg_app      what gunicorn connects as. It can read and write the data tables, but it can
--                   NOT drop or alter anything, and it can only ever APPEND to the audit log and
--                   the usage log. A bug (or an attacker) in the web process therefore cannot
--                   rewrite history through the database connection it holds.
--
-- Put dsoreg_app's password in DATABASE_URL (/etc/dsoreg/backend.env, mode 640 root:dsoreg).

CREATE ROLE dsoreg_app LOGIN PASSWORD 'CHANGE-ME-LONG-RANDOM';
GRANT CONNECT ON DATABASE dsoreg TO dsoreg_app;
GRANT USAGE ON SCHEMA public TO dsoreg_app;

GRANT SELECT, INSERT, UPDATE ON users, devices, asset_profile, asset_state, fault_report TO dsoreg_app;
GRANT SELECT, INSERT         ON assets, usage_log, idempotency, audit_log          TO dsoreg_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limit                                 TO dsoreg_app;
GRANT USAGE, SELECT ON SEQUENCE audit_log_seq_seq TO dsoreg_app;

-- idempotency rows are only ever inserted; an old one may be purged by a maintenance job
-- running as dsoreg_migrate, not by the web process.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log, usage_log, assets FROM dsoreg_app;

-- Supabase-style public roles must not be able to touch these tables at all.
-- (If the database is ever exposed through a REST layer, enable row-level security first.)
ALTER TABLE users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE devices        ENABLE ROW LEVEL SECURITY;
ALTER TABLE assets         ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_profile  ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_state    ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE fault_report   ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limit     ENABLE ROW LEVEL SECURITY;
-- The owner (dsoreg_migrate) bypasses RLS; dsoreg_app needs an explicit permissive policy:
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','devices','assets','asset_profile','asset_state','usage_log',
                           'fault_report','idempotency','audit_log','rate_limit']
  LOOP
    EXECUTE format('CREATE POLICY app_all ON %I FOR ALL TO dsoreg_app USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;
