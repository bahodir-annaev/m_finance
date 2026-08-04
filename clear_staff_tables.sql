-- =============================================================================
-- MIZAN Finance — clear staff and all tables that reference staff.id
--
-- Precondition for seed_staff_departments_roles.sql, which drops and
-- reinserts staff/departments/staff_roles and assumes referencing tables are
-- already empty.
--
-- Tables with a required staff_id FK are fully cleared (their rows have no
-- meaning without a staff row): salary_history, project_hours,
-- personal_equipment, personal_licenses, period_allocations, milestone_staff.
--
-- transactions.responsible_id / dividends.responsible_id are optional FKs on
-- otherwise-unrelated financial records — those rows carry real cash-flow
-- history, so only the FK column is nulled, not the row.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Nullable staff FK on financial tables — keep the rows, drop the link ────
UPDATE transactions SET responsible_id = NULL WHERE responsible_id IS NOT NULL;
UPDATE dividends SET responsible_id = NULL WHERE responsible_id IS NOT NULL;

-- ── Tables where staff_id is required — clear entirely ──────────────────────
DELETE FROM period_allocations;
DELETE FROM milestone_staff;
DELETE FROM personal_licenses;
DELETE FROM personal_equipment;
DELETE FROM project_hours;
DELETE FROM salary_history;

DELETE FROM sqlite_sequence WHERE name IN (
    'period_allocations', 'milestone_staff', 'personal_licenses',
    'personal_equipment', 'project_hours', 'salary_history'
);

-- ── Staff + lookup tables themselves ─────────────────────────────────────────
DELETE FROM staff;
DELETE FROM departments;
DELETE FROM staff_roles;
DELETE FROM sqlite_sequence WHERE name IN ('staff', 'departments', 'staff_roles');

COMMIT;

PRAGMA foreign_keys = ON;
