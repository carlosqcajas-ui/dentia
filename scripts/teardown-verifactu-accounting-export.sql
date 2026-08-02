-- One-shot teardown for the removed `verifactu` and `accounting_export`
-- modules. Run ONCE per database, after deploying the commit that
-- deletes those modules and BEFORE the next `alembic upgrade heads`.
--
-- Why a raw script and not a migration: Alembic resolves every row in
-- `alembic_version` against the scripts on disk *before* running
-- anything. With the two branches deleted, `vfy_0006` and `accexp_0001`
-- are unresolvable and `alembic upgrade heads` aborts with
-- "Can't locate revision identified by 'vfy_0006'". A migration can't
-- repair that — it never gets to run. So the orphan branch rows have to
-- go out of band.
--
-- Safety notes:
--   * `accounting_export` created no tables — its migration was a
--     placeholder. Only its branch row needs removing.
--   * `verifactu` owned exactly the 5 tables below. Its FKs pointed
--     *into* `invoices` / `clinics` / `users`, never the reverse, so
--     dropping them cannot cascade into billing data.
--   * Verify the tables are empty before running (they are on the
--     current Droplet — all five returned 0 rows on 2026-08-02):
--
--       SELECT 'verifactu_records', count(*) FROM verifactu_records
--       UNION ALL SELECT 'verifactu_record_attempts', count(*) FROM verifactu_record_attempts
--       UNION ALL SELECT 'verifactu_certificates', count(*) FROM verifactu_certificates
--       UNION ALL SELECT 'verifactu_settings', count(*) FROM verifactu_settings
--       UNION ALL SELECT 'verifactu_vat_classifications', count(*) FROM verifactu_vat_classifications;

BEGIN;

-- Child-first, though CASCADE would handle it.
DROP TABLE IF EXISTS verifactu_record_attempts;
DROP TABLE IF EXISTS verifactu_records;
DROP TABLE IF EXISTS verifactu_vat_classifications;
DROP TABLE IF EXISTS verifactu_certificates;
DROP TABLE IF EXISTS verifactu_settings;

-- Orphan Alembic branch heads. Without this, `alembic upgrade heads`
-- fails on the next deploy.
DELETE FROM alembic_version WHERE version_num IN ('vfy_0006', 'accexp_0001');

-- Module registry rows (both were already in state 'uninstalled').
DELETE FROM core_module_operation_log
 WHERE module_name IN ('verifactu', 'accounting_export');
DELETE FROM core_module
 WHERE name IN ('verifactu', 'accounting_export');

COMMIT;
