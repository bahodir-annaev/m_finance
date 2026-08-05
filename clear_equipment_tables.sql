-- =============================================================================
-- MIZAN Finance — clear personal_equipment and general_equipment
--
-- Precondition for seed_equipment_amortizatsiya.sql, which drops and
-- reinserts both tables from scratch. Nothing else has a foreign key into
-- personal_equipment.id or general_equipment.id (period_allocations stores a
-- computed snapshot amount, not a reference), so both tables can be cleared
-- outright with no cascading cleanup required.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

DELETE FROM personal_equipment;
DELETE FROM general_equipment;
DELETE FROM sqlite_sequence WHERE name IN ('personal_equipment', 'general_equipment');

COMMIT;

PRAGMA foreign_keys = ON;
