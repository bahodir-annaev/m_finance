-- =============================================================================
-- MIZAN Finance -- personal_equipment / general_equipment seed from
-- "Амортизация new.xlsx", sheet "ОС" (Telegram Desktop export, received 2026-08-05)
--
-- Scope: personal_equipment, general_equipment ONLY.
-- Each source row bundles up to 3 physical assets sharing one purchase date:
--   1) main unit  (PC / laptop / monoblok / printer / TV / desk+chair set)
--   2) monitor(s) (name + total cost for the monitor(s) on that row)
--   3) UPS / mouse+keyboard bundle
-- These are inserted as separate equipment rows (matching how the app tracks
-- and depreciates each item independently). Inventory numbers from column C are
-- kept in the item name in parentheses for traceability back to the source sheet.
--
-- lifespan_months = 36 for every row (per the source sheet's single depreciation
-- period column M, verified against column N = row total / 36 for every line).
--
-- Linking: staff_id is resolved via subquery on staff.full_name, so this script
-- is safe to run regardless of staff.id ordering, as long as
-- seed_staff_departments_roles.sql (or equivalent) has already been applied.
--
-- Unmatched owners: some rows name an employee who is NOT in the current 38-person
-- staff roster (former employees, external/partner staff, or -- in one case --
-- a probable first-name typo/mismatch). These are still inserted into
-- personal_equipment so the asset and its cost are not lost, but with
-- staff_id = NULL and the original Excel employee name kept in the item name.
-- Review the 'UNMATCHED OWNERS' block below and re-assign staff_id manually
-- (or via the Equipment page UI) once the correct person is confirmed.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Drop old data ────────────────────────────────────────────────────────────
DELETE FROM personal_equipment;
DELETE FROM general_equipment;
DELETE FROM sqlite_sequence WHERE name IN ('personal_equipment', 'general_equipment');

-- ── personal_equipment — matched to staff roster ────────────────────────────
-- Administrasiya
-- Behzod — Niyazov Behzod Xakimovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte Z77-D3H /core i7-3770 / 2x 8 gb / 1.8 TB + 112GB + 2.7TB/NIVIDIA Geforce GTX 670 2GB) (ИНВ-00001)', (SELECT id FROM staff WHERE full_name = 'Niyazov Behzod Xakimovich'), 25000000, 36, '2024-09-10', 1),
('Redmi A27Q 2025 2шт', (SELECT id FROM staff WHERE full_name = 'Niyazov Behzod Xakimovich'), 3268000, 36, '2024-09-10', 1),
('logitech  Мышь +Клав.', (SELECT id FROM staff WHERE full_name = 'Niyazov Behzod Xakimovich'), 2500000, 36, '2024-09-10', 1),
('Стол с тумбой, Кресло (ИНВ-М003)', (SELECT id FROM staff WHERE full_name = 'Niyazov Behzod Xakimovich'), 3758750, 36, '2024-11-23', 1);

-- Umar R — Risbekov Umar Ag'zam o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Monoblok HP EliteOne 1000 G2(HP 83E5 /  Core i7 / 16 GB / SSD 512gb / RX 560X Series 4gb  +Intel Graphics 1gb / Mi Monitor 1x) (ИНВ-00023)', (SELECT id FROM staff WHERE full_name = 'Risbekov Umar Ag''zam o''g''li'), 5626100, 36, '2024-08-09', 1),
('Mi Monitor 1шт', (SELECT id FROM staff WHERE full_name = 'Risbekov Umar Ag''zam o''g''li'), 1634000, 36, '2024-08-09', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М014)', (SELECT id FROM staff WHERE full_name = 'Risbekov Umar Ag''zam o''g''li'), 3465000, 36, '2025-11-01', 1);

-- Orifjon — Obidov Orifjon Yusupovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук HP Victus ( Core i5 , 2x8gb, SSD 512 gb, RTX 3050 4gb, BenQ GW2780 2x monitor) (ИНВ-00029)', (SELECT id FROM staff WHERE full_name = 'Obidov Orifjon Yusupovich'), 9821000, 36, '2024-12-14', 1),
('BenQ GW2780 2x monitor', (SELECT id FROM staff WHERE full_name = 'Obidov Orifjon Yusupovich'), 2700000, 36, '2024-12-14', 1),
('logitech  Мышь +Клав.', (SELECT id FROM staff WHERE full_name = 'Obidov Orifjon Yusupovich'), 2500000, 36, '2024-12-14', 1),
('Стол с тумбой, Кресло (ИНВ-М004)', (SELECT id FROM staff WHERE full_name = 'Obidov Orifjon Yusupovich'), 3758750, 36, '2024-11-23', 1);

-- Nurxayot — Abralov Nurxayot Ilyas o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Abralov Nurxayot Ilyas o''g''li'), 3268000, 36, NULL, 1),
('logitech  Мышь +Клав.', (SELECT id FROM staff WHERE full_name = 'Abralov Nurxayot Ilyas o''g''li'), 440178.57, 36, NULL, 1),
('Стол с тумбой, Кресло (ИНВ-М008)', (SELECT id FROM staff WHERE full_name = 'Abralov Nurxayot Ilyas o''g''li'), 3760000, 36, '2026-03-28', 1);

-- Texnik hisob-kitob
-- Ruslana — Halilova Ruslana Sitmerovna
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук ASUSTeK Vivobook_ASUSLaptop ( Core i7 , 16gb, SSD 1tb,Intel Graphics 1gb, BenQ GW2780 Monitor) (ИНВ-00034)', (SELECT id FROM staff WHERE full_name = 'Halilova Ruslana Sitmerovna'), 7589000, 36, '2025-01-14', 1),
('BenQ GW2780  monitor', (SELECT id FROM staff WHERE full_name = 'Halilova Ruslana Sitmerovna'), 1350000, 36, '2025-01-14', 1),
('Стол с тумбой, Кресло (ИНВ-М010)', (SELECT id FROM staff WHERE full_name = 'Halilova Ruslana Sitmerovna'), 3760000, 36, '2026-02-02', 1);

-- Grafik dizayn
-- Adizjon — Saidov Adizjon Qaxramonovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte X670E AORUS PRO X / Ryzen 9 7950X / 2x 32gb DDR5 / SSD 1tb +HDD 2tb /"AMD Radeon 0.5 gb + NVIDIA GeForce RTX 5070 11.9 gb" / Mi Monitor 2шт / UPS	) (ИНВ-00003)', (SELECT id FROM staff WHERE full_name = 'Saidov Adizjon Qaxramonovich'), 26180000, 36, '2025-11-30', 1),
('Mi Monitor 2 шт', (SELECT id FROM staff WHERE full_name = 'Saidov Adizjon Qaxramonovich'), 3268000, 36, '2025-11-30', 1),
('AVT1500Z-LI (UPS)', (SELECT id FROM staff WHERE full_name = 'Saidov Adizjon Qaxramonovich'), 3204960, 36, '2025-11-30', 1),
('Стол, Кресло (ИНВ-М040)', (SELECT id FROM staff WHERE full_name = 'Saidov Adizjon Qaxramonovich'), 2632500, 36, '2024-08-02', 1);

-- Master plan
-- Shohzod — Rahmatov Shohzod Karim o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK TUF GAMING X670E-PLUS WIFI /Ryzen 9 7950X / 2x 32gb  RAM / SSD 1TB + HDD 4TB / "AMD Graphics 0.5gb +  RTX 4070  12gb" / Mi Monitor 2шт) (ИНВ-00005)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Shohzod Karim o''g''li'), 32375000, 36, '2024-08-28', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Rahmatov Shohzod Karim o''g''li'), 3268000, 36, '2024-08-28', 1),
('AVT1500Z-LI (UPS)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Shohzod Karim o''g''li'), 3204960, 36, '2024-08-28', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М012)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Shohzod Karim o''g''li'), 3465000, 36, '2025-11-01', 1);

-- Umidjon — Raxmatov Umidjon Mirfoziljon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Micro-Star MAG X670E /  Ryzen 9  / 2x 32 GB / SSD 1tb / RTX 5070 12gb + AMD Radeon 2gb / Mi Monitor 2x) (ИНВ-00012)', (SELECT id FROM staff WHERE full_name = 'Raxmatov Umidjon Mirfoziljon o''g''li'), 26180000, 36, '2025-11-30', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Raxmatov Umidjon Mirfoziljon o''g''li'), 3268000, 36, '2025-11-30', 1),
('ION V-1000T(UPS)', (SELECT id FROM staff WHERE full_name = 'Raxmatov Umidjon Mirfoziljon o''g''li'), 1014000, 36, '2025-11-30', 1),
('Стол, Кресло (ИНВ-М039)', (SELECT id FROM staff WHERE full_name = 'Raxmatov Umidjon Mirfoziljon o''g''li'), 2632500, 36, '2024-08-02', 1);

-- BIM
-- Aziz O — Omonov Aziz Farxod o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK TUF GAMING X670E-PLUS /  Ryzen 9  / 2x 16 GB / 2x SSD 1tb + HDD 1tb / RTX 3070 Ti 8GB + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00013)', (SELECT id FROM staff WHERE full_name = 'Omonov Aziz Farxod o''g''li'), 26447750, 36, '2024-07-26', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Omonov Aziz Farxod o''g''li'), 3268000, 36, '2024-07-26', 1),
('Стол с тумбой, Кресло (ИНВ-М001)', (SELECT id FROM staff WHERE full_name = 'Omonov Aziz Farxod o''g''li'), 3618500, 36, '2024-07-19', 1);

-- Zafarjon — Rahmatov Zafarjon G'ayrat o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Micro-Star B850/  Ryzen 9  / 2x 32 GB / SSD 1tb + HDD 2tb / RTX 4060 8gb + AMD Radeon 2gb / Mi Monitor 2x) (ИНВ-00014)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Zafarjon G''ayrat o''g''li'), 26447750, 36, '2024-07-26', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Rahmatov Zafarjon G''ayrat o''g''li'), 3268000, 36, '2024-07-26', 1),
('ION V-1200T(UPS)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Zafarjon G''ayrat o''g''li'), 465000, 36, '2024-07-26', 1),
('Стол с тумбой, Кресло (ИНВ-М002)', (SELECT id FROM staff WHERE full_name = 'Rahmatov Zafarjon G''ayrat o''g''li'), 3618500, 36, '2024-07-19', 1);

-- Ibrohim — Islomov Ibrohim Ilxomjon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Islomov Ibrohim Ilxomjon o''g''li'), 3268000, 36, NULL, 1),
('AVT1200X-LI-LED', (SELECT id FROM staff WHERE full_name = 'Islomov Ibrohim Ilxomjon o''g''li'), 465000, 36, NULL, 1),
('Стол, Кресло (ИНВ-М030)', (SELECT id FROM staff WHERE full_name = 'Islomov Ibrohim Ilxomjon o''g''li'), 2700000, 36, '2026-04-18', 1);

-- Xasan — G'anixo'jayev Xasan Saidumar o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte X670 AORUS ELITE AX /  Ryzen 9  / 4x 16 GB / SSD 512gb / RTX 3070 Ti  8gb + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00016)', (SELECT id FROM staff WHERE full_name = 'G''anixo''jayev Xasan Saidumar o''g''li'), 25200406, 36, '2025-06-04', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'G''anixo''jayev Xasan Saidumar o''g''li'), 3268000, 36, '2025-06-04', 1),
('ION V-1000 LCD', (SELECT id FROM staff WHERE full_name = 'G''anixo''jayev Xasan Saidumar o''g''li'), 1174107.14, 36, '2025-06-04', 1),
('Стол, Кресло (ИНВ-М027)', (SELECT id FROM staff WHERE full_name = 'G''anixo''jayev Xasan Saidumar o''g''li'), 2700000, 36, '2026-01-30', 1);

-- Abror — Pirimqulov Abror Faxriddin o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол, Кресло (ИНВ-М049)', (SELECT id FROM staff WHERE full_name = 'Pirimqulov Abror Faxriddin o''g''li'), 2647900, 36, '2024-07-19', 1);

-- Iskandar — Xudoyberdiyev Iskandar Bahromovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte Z390 UD /  Core i7-9700 / 2x 16GB / 2x SSD (1tb+265) + HDD 1 tb / RTX 3070 Ti 8gb/ Mi Monitor 2x) (ИНВ-00020)', (SELECT id FROM staff WHERE full_name = 'Xudoyberdiyev Iskandar Bahromovich'), 15000000, 36, '2024-09-10', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Xudoyberdiyev Iskandar Bahromovich'), 3268000, 36, '2024-09-10', 1),
('AVT1200X-LI-LED', (SELECT id FROM staff WHERE full_name = 'Xudoyberdiyev Iskandar Bahromovich'), 465000, 36, '2024-09-10', 1),
('Стол, Кресло (ИНВ-М031)', (SELECT id FROM staff WHERE full_name = 'Xudoyberdiyev Iskandar Bahromovich'), 2700000, 36, '2026-04-18', 1);

-- Abdurashid — Abdug'ofurov Abdurashid Moxir o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK TUF GAMING X570-PLUS /  Ryzen 9 / 2x 32 GB / SSD 1tb + HDD 4 tb / RTX 3060 12gb  / Mi Monitor 2x) (ИНВ-00018)', (SELECT id FROM staff WHERE full_name = 'Abdug''ofurov Abdurashid Moxir o''g''li'), 20000000, 36, '2024-09-10', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Abdug''ofurov Abdurashid Moxir o''g''li'), 3268000, 36, '2024-09-10', 1),
('Стол, Кресло (ИНВ-М029)', (SELECT id FROM staff WHERE full_name = 'Abdug''ofurov Abdurashid Moxir o''g''li'), 2700000, 36, '2026-04-15', 1);

-- Javoxir — Murodov Javoxir Avaz o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK PRIME X870-P /  Ryzen 9  / 2x 16 GB / SSD 512gb / RTX 5060 8gb + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00015)', (SELECT id FROM staff WHERE full_name = 'Murodov Javoxir Avaz o''g''li'), 21400000, 36, '2026-04-13', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Murodov Javoxir Avaz o''g''li'), 3268000, 36, '2026-04-13', 1),
('Стол, Кресло (ИНВ-М026)', (SELECT id FROM staff WHERE full_name = 'Murodov Javoxir Avaz o''g''li'), 2700000, 36, '2026-01-30', 1);

-- AI integratsiya
-- Davron — Djurayev Davron Batirovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук ASUSTeK ROG Strix G16 ( Ryzen 9 , 2x32gb, SSD 1tb, RTX 5070 11.9gb, Mi Monitor) (ИНВ-00033)', (SELECT id FROM staff WHERE full_name = 'Djurayev Davron Batirovich'), 37315000, 36, '2026-04-08', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Djurayev Davron Batirovich'), 3268000, 36, '2026-04-08', 1),
('Стол с тумбой, Кресло (ИНВ-М009)', (SELECT id FROM staff WHERE full_name = 'Djurayev Davron Batirovich'), 3760000, 36, '2026-04-18', 1);

-- Madina — Berdiyeva Madina Ganjiyevna
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Micro-Star PRO H610M-E /Core i5 / 2x 8gb / 512GB SSD / Intel UHD Graphics 2gb/DELL Monitor ) (ИНВ-00038)', (SELECT id FROM staff WHERE full_name = 'Berdiyeva Madina Ganjiyevna'), 4300000, 36, '2024-09-10', 1),
('Стол, Кресло (ИНВ-М019)', (SELECT id FROM staff WHERE full_name = 'Berdiyeva Madina Ganjiyevna'), 2700000, 36, '2024-12-26', 1);

-- Vizualizatsiya
-- Islom — Jo'rayev Islom Botirovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte X670/ AMD Ryzen Threadripper PRO 7995WX / 8x 64 GB / SSD 4tb / 2x RTX  5090 31.8 gb  +ASPEED Graphics Family 0.1gb / Mi Monitor 2x) (ИНВ-00025)', (SELECT id FROM staff WHERE full_name = 'Jo''rayev Islom Botirovich'), 335400000, 36, '2025-05-07', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Jo''rayev Islom Botirovich'), 3268000, 36, '2025-05-07', 1),
('Стол, Кресло (ИНВ-М024)', (SELECT id FROM staff WHERE full_name = 'Jo''rayev Islom Botirovich'), 2700000, 36, '2024-12-26', 1);

-- Abdulla — Abdullayev Abdulla Mansurjonovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK ROG STRIX X670E-E /  Ryzen 9 / 4x 32 GB / SSD 1tb + HDD 3x / RTX  4080 SUPER 16gb  + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00026)', (SELECT id FROM staff WHERE full_name = 'Abdullayev Abdulla Mansurjonovich'), 41175892.86, 36, '2025-01-03', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Abdullayev Abdulla Mansurjonovich'), 3268000, 36, '2025-01-03', 1),
('ION V-1000 LCD', (SELECT id FROM staff WHERE full_name = 'Abdullayev Abdulla Mansurjonovich'), 1174107.14, 36, '2025-01-03', 1),
('Стол, Кресло (ИНВ-М025)', (SELECT id FROM staff WHERE full_name = 'Abdullayev Abdulla Mansurjonovich'), 2700000, 36, '2025-07-26', 1);

-- Umar S — Sharipov Umar Ortiq o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK ROG STRIX B650E-F /  Ryzen 9 / 2x 32 GB / SSD 1tb + HDD 2 tb / RTX  5070 11.9gb  + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00022)', (SELECT id FROM staff WHERE full_name = 'Sharipov Umar Ortiq o''g''li'), 37074107.14, 36, '2024-08-28', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Sharipov Umar Ortiq o''g''li'), 3268000, 36, '2024-08-28', 1),
('Стол, Кресло (ИНВ-М033)', (SELECT id FROM staff WHERE full_name = 'Sharipov Umar Ortiq o''g''li'), 2700000, 36, '2026-04-18', 1);

-- Ramziddin — Muxutdinov Ramziddin Nizamitdinovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK  ROG STRIX B650E-F /  Ryzen 9 / 4x 32 GB / SSD 1tb + HDD 4tb / RTX  4080 SUPER 16gb  + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00027)', (SELECT id FROM staff WHERE full_name = 'Muxutdinov Ramziddin Nizamitdinovich'), 44500000, 36, '2024-09-10', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Muxutdinov Ramziddin Nizamitdinovich'), 3268000, 36, '2024-09-10', 1),
('A1500', (SELECT id FROM staff WHERE full_name = 'Muxutdinov Ramziddin Nizamitdinovich'), 3204960, 36, '2024-09-10', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М015)', (SELECT id FROM staff WHERE full_name = 'Muxutdinov Ramziddin Nizamitdinovich'), 3465000, 36, '2025-11-01', 1);

-- Shaxboz — Umataliyev Shaxboz Narimon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte X670/  Ryzen 9 / 4x 32 GB / SSD 1tb + HDD 4tb / RTX  4080 SUPER 16gb  + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00024)', (SELECT id FROM staff WHERE full_name = 'Umataliyev Shaxboz Narimon o''g''li'), 36260000, 36, NULL, 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Umataliyev Shaxboz Narimon o''g''li'), 3268000, 36, NULL, 1),
('ION V-1000 LCD', (SELECT id FROM staff WHERE full_name = 'Umataliyev Shaxboz Narimon o''g''li'), 1174107.14, 36, NULL, 1),
('Стол, Кресло (ИНВ-М023)', (SELECT id FROM staff WHERE full_name = 'Umataliyev Shaxboz Narimon o''g''li'), 2700000, 36, '2024-12-26', 1);

-- Konstruksiya
-- Zoirjon — Axmadaliyev Zoirjon Zohidjon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте  (Micro-Star MAG X670E / Ryzen 7 9800X3D / 2x 32gb / 1TB ssd / AMD Radeon 2gb + RTX 5070 11.9gb / Mi Monitor 27A1 2 шт ) (ИНВ-00007)', (SELECT id FROM staff WHERE full_name = 'Axmadaliyev Zoirjon Zohidjon o''g''li'), 23795600, 36, '2025-09-03', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Axmadaliyev Zoirjon Zohidjon o''g''li'), 3268000, 36, '2025-09-03', 1),
('KS1200(UPS)', (SELECT id FROM staff WHERE full_name = 'Axmadaliyev Zoirjon Zohidjon o''g''li'), 465000, 36, '2025-09-03', 1),
('Стол, Кресло (ИНВ-М034)', (SELECT id FROM staff WHERE full_name = 'Axmadaliyev Zoirjon Zohidjon o''g''li'), 2700000, 36, '2026-04-18', 1);

-- Rovshan — Egamberdiyev Rovshan Abduqodir o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK B850 /  Ryzen 9  / 2x 16 GB / SSD 512gb / RTX 5060 8gb + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00008)', (SELECT id FROM staff WHERE full_name = 'Egamberdiyev Rovshan Abduqodir o''g''li'), 21400000, 36, '2026-04-18', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Egamberdiyev Rovshan Abduqodir o''g''li'), 3268000, 36, '2026-04-18', 1),
('Стол, Кресло (ИНВ-М035)', (SELECT id FROM staff WHERE full_name = 'Egamberdiyev Rovshan Abduqodir o''g''li'), 2700000, 36, '2026-04-18', 1);

-- Elektrika
-- Baxodir — Mamedov Baxodir Guychmatovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук Acer Predator PHN16-71 (i9-13900HX, 2шт 16gb, 512gb SSD, Mi Monitor 2шт) (ИНВ-00006)', (SELECT id FROM staff WHERE full_name = 'Mamedov Baxodir Guychmatovich'), 13320000, 36, '2026-04-11', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Mamedov Baxodir Guychmatovich'), 3268000, 36, '2026-04-11', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М013)', (SELECT id FROM staff WHERE full_name = 'Mamedov Baxodir Guychmatovich'), 3465000, 36, '2025-11-01', 1);

-- Atamurat — Kamalov Atamurat Raxatovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK PRIME X670-P/  Ryzen 9  / 2x 16 GB / SSD 512gb / RTX 5060 8gb + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00009)', (SELECT id FROM staff WHERE full_name = 'Kamalov Atamurat Raxatovich'), 19031250, 36, '2026-04-11', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Kamalov Atamurat Raxatovich'), 3268000, 36, '2026-04-11', 1),
('Стол, Кресло (ИНВ-М036)', (SELECT id FROM staff WHERE full_name = 'Kamalov Atamurat Raxatovich'), 2700000, 36, '2026-04-18', 1);

-- Abdulxafizxon — Saidov Abdulxafizxon Murodjon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK PRIME X670-P /  Ryzen 9  / 2x 16 GB / SSD 512gb / RTX 5060 8gb + AMD Radeon 0,5gb / Mi Monitor 2x) (ИНВ-00010)', (SELECT id FROM staff WHERE full_name = 'Saidov Abdulxafizxon Murodjon o''g''li'), 19031250, 36, '2026-04-11', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Saidov Abdulxafizxon Murodjon o''g''li'), 3268000, 36, '2026-04-11', 1),
('Стол, Кресло (ИНВ-М037)', (SELECT id FROM staff WHERE full_name = 'Saidov Abdulxafizxon Murodjon o''g''li'), 2700000, 36, '2026-04-18', 1);

-- Saidbek — Usmonaliyev Saidbek Jamoliddin o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол, Кресло (ИНВ-М048)', (SELECT id FROM staff WHERE full_name = 'Usmonaliyev Saidbek Jamoliddin o''g''li'), 2647900, 36, '2024-07-19', 1);

-- Interyer
-- Aziz M — Muxamedov Aziz Sandjarovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Gigabyte B650 / Rayzen 7950X / 2x 32 gb DDR5 RAM / HDD 2tb + SSD 512gb / RTX 3060 12gb + AMD Radeon™ graphics 0.5 gb" ) (ИНВ-00004)', (SELECT id FROM staff WHERE full_name = 'Muxamedov Aziz Sandjarovich'), 36280000, 36, '2025-04-28', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Muxamedov Aziz Sandjarovich'), 3268000, 36, '2025-04-28', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М018)', (SELECT id FROM staff WHERE full_name = 'Muxamedov Aziz Sandjarovich'), 3465000, 36, '2025-11-01', 1);

-- Olchinbek — Olimov Olchinbek Olim o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK ROG STRIX B850-A /  Ryzen 7  / 2x 32 GB / SSD 1tb + HDD 2 tb / RTX 5070 11.9 gb + AMD Radeon 2gb / Mi Monitor 2x) (ИНВ-00017)', (SELECT id FROM staff WHERE full_name = 'Olimov Olchinbek Olim o''g''li'), 26180000, 36, '2025-11-30', 1),
('Mi Monitor 2шт', (SELECT id FROM staff WHERE full_name = 'Olimov Olchinbek Olim o''g''li'), 3268000, 36, '2025-11-30', 1),
('Стол, Кресло (ИНВ-М028)', (SELECT id FROM staff WHERE full_name = 'Olimov Olchinbek Olim o''g''li'), 2700000, 36, '2026-01-30', 1);

-- ── personal_equipment — UNMATCHED OWNERS (staff_id left NULL, review manually) ──
-- Original Excel employee name is embedded in each item's name for reference.
-- Excel employee: Abdullayev Abbosbek G'ayratjon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (Micro-Starv Z790 GAMING PRO / Core i9-13900K / 4x 16 GB / SSD 1tb + HDD 4 tb / RTX 4080 16gb + Intel Graphics 2gb/ Mi Monitor 2x) (ИНВ-00021) [Abdullayev Abbosbek G''ayratjon o''g''li]', NULL, 26447750, 36, '2024-07-26', 1),
('Mi Monitor 2шт [Abdullayev Abbosbek G''ayratjon o''g''li]', NULL, 3268000, 36, '2024-07-26', 1),
('A-1500 [Abdullayev Abbosbek G''ayratjon o''g''li]', NULL, 3204960, 36, '2024-07-26', 1),
('Стол, Кресло (ИНВ-М032) [Abdullayev Abbosbek G''ayratjon o''g''li]', NULL, 2700000, 36, '2026-04-18', 1);

-- Excel employee: Ayhan sucu
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол, Кресло (ИНВ-М045) [Ayhan sucu]', NULL, 2647900, 36, '2024-07-19', 1);

-- Excel employee: Aytunc Ozgur
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK TUF GAMING B850-PLUS WIFI/ Ryzen 7 / 4x 16gb / 1TB SSD / RTX 5070 12gb + AMD Radeon 2gb /Mi Monitor 2x) (ИНВ-00036) [Aytunc Ozgur]', NULL, 25914000, 36, '2025-12-12', 1),
('Mi Monitor 2шт [Aytunc Ozgur]', NULL, 3268000, 36, '2025-12-12', 1),
('Стол, Кресло (ИНВ-М021) [Aytunc Ozgur]', NULL, 2700000, 36, '2024-12-26', 1);

-- Excel employee: Ender Kahraman
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK ROG STRIX B850-A GAMING WIFI / Ryzen 7 / 2x 32gb / 1TB SSD  + HDD 2tb  / RTX 5070 12gb + AMD Radeon 2gb /Mi Monitor 2x) (ИНВ-00035) [Ender Kahraman]', NULL, 25914000, 36, '2025-12-12', 1),
('Mi Monitor 2шт [Ender Kahraman]', NULL, 3268000, 36, '2025-12-12', 1),
('Стол, Кресло (ИНВ-М020) [Ender Kahraman]', NULL, 2700000, 36, '2024-12-26', 1);

-- Excel employee: Faxriddinov Ziyoviddin Sadriddin o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Mi Monitor 2шт [Faxriddinov Ziyoviddin Sadriddin o''g''li]', NULL, 3268000, 36, NULL, 1),
('AVT1500Z-LI [Faxriddinov Ziyoviddin Sadriddin o''g''li]', NULL, 3204960, 36, NULL, 1),
('Стол, Кресло (ИНВ-М022) [Faxriddinov Ziyoviddin Sadriddin o''g''li]', NULL, 2700000, 36, '2024-12-26', 1);

-- Excel employee: Inagamov Farxod Baxtiyarovich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK  ROG STRIX B850-A  / Ryzen 7 9800X3D / 2X 32GB RAM / 1TB SSD,  1.8 TB HDD / RTX 5070 4GB + AMD Radeon 2 GB" ) (ИНВ-00002) [Inagamov Farxod Baxtiyarovich]', NULL, 23795600, 36, '2025-08-20', 1),
('Mi Monitor 2 шт [Inagamov Farxod Baxtiyarovich]', NULL, 3268000, 36, '2025-08-20', 1),
('ION V-1000 LCD (UPS) [Inagamov Farxod Baxtiyarovich]', NULL, 1174107.14, 36, '2025-08-20', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М017) [Inagamov Farxod Baxtiyarovich]', NULL, 3465000, 36, '2025-11-01', 1);

-- Excel employee: Jamolbek Toshyev
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK  B850 /  Ryzen 9  / 2x 32 GB / SSD 1tb / RTX 5060 8gb + AMD Radeon 0,5gb ) (ИНВ-00011) [Jamolbek Toshyev]', NULL, 25697000, 36, '2026-04-18', 1),
('DELL U2717D 2x [Jamolbek Toshyev]', NULL, 3268000, 36, '2026-04-18', 1),
('ION V-1000T(UPS) [Jamolbek Toshyev]', NULL, 1014000, 36, '2026-04-18', 1),
('Стол, Кресло (ИНВ-М038) [Jamolbek Toshyev]', NULL, 2632500, 36, '2024-08-02', 1);

-- Excel employee: Kadir Ozdemir
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол с тумбой, Кресло (ИНВ-М011) [Kadir Ozdemir]', NULL, 3760000, 36, '2026-02-02', 1);

-- Excel employee: Kunnazarov Timur Seilxon o'g'li
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук HP Victus ( Core i5 ,16gb ram, SSD 512 gb, RTX 4050 6gb, Mi Monitor) (ИНВ-00031) [Kunnazarov Timur Seilxon o''g''li]', NULL, 9134000, 36, '2026-03-28', 1),
('Mi Monitor 1шт [Kunnazarov Timur Seilxon o''g''li]', NULL, 1634000, 36, '2026-03-28', 1),
('Стол с тумбой, Кресло (ИНВ-М007) [Kunnazarov Timur Seilxon o''g''li]', NULL, 3760000, 36, '2026-01-30', 1);

-- Excel employee: Murat özkan
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол, Кресло (ИНВ-М046) [Murat özkan]', NULL, 2647900, 36, '2024-07-19', 1);

-- Excel employee: Ravshonxonov Ziyodulloxon
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Ноутбук HP Victus ( Core i5 , 2x8gb, SSD 512 gb, RTX 3050 4gb, BenQ GW2780) (ИНВ-00030) [Ravshonxonov Ziyodulloxon]', NULL, 9821000, 36, '2024-12-14', 1),
('Monitor [Ravshonxonov Ziyodulloxon]', NULL, 4400000, 36, '2024-12-14', 1),
('logitech  Мышь +Клав. [Ravshonxonov Ziyodulloxon]', NULL, 2500000, 36, '2024-12-14', 1),
('Стол с тумбой, Кресло (ИНВ-М005) [Ravshonxonov Ziyodulloxon]', NULL, 3758750, 36, '2024-11-23', 1);

-- Excel employee: Turayev Kamil
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('ПК в комплекте (ASUSTeK TUF GAMING X670E-PLUS WIFI/ Ryzen 7 / 2x 32gb / 1TB SSD / RTX 5070 12gb + AMD Radeon 2gb /Mi Monitor 2x) (ИНВ-00037) [Turayev Kamil]', NULL, 25914000, 36, '2025-12-12', 1),
('Mi Monitor 2шт [Turayev Kamil]', NULL, 3268000, 36, '2025-12-12', 1),
('Стол,Тумба мобильная,Кресло (ИНВ-М016) [Turayev Kamil]', NULL, 3465000, 36, '2025-11-01', 1);

-- Excel employee: Xomidov Nurmuhammad Nusratillayevich
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Mi Monitor 2шт [Xomidov Nurmuhammad Nusratillayevich]', NULL, 3268000, 36, NULL, 1),
('ION V-1000T(UPS) [Xomidov Nurmuhammad Nusratillayevich]', NULL, 1014000, 36, NULL, 1);

-- Excel employee: Üzeyir Altunöz
INSERT INTO personal_equipment (name, staff_id, price, lifespan_months, purchase_date, is_active) VALUES
('Стол, Кресло (ИНВ-М047) [Üzeyir Altunöz]', NULL, 2647900, 36, '2024-07-19', 1);

-- ── general_equipment — АУП / shared / unowned items ────────────────────────
INSERT INTO general_equipment (name, quantity, price, lifespan_months, purchase_date, is_active) VALUES
('Принтер Epson L1800 модел B472C (INV-001P)', 1, 10694642.86, 36, '2024-08-12', 1),
('Canon C3926i Model: F810300 (INV-002P)', 1, 33759000, 36, '2024-12-19', 1),
('Canon F810100 sn: 5CR12793 MF752cdw (INV-004P)', 1, 5000000, 36, '2026-04-23', 1),
('Телевизор SAMSUNG QLED The Frame 75LS03BAU 4K UHD Smart TV 75, (ИНВ-001T)', 1, 29200000, 36, '2024-12-23', 1),
('Телевизор SAMSUNG QLED The Frame QE85LS03DAUXCE  4K UHD Smart TV 85, + М4 pro mac mini (ИНВ-002T)', 1, 53535000, 36, '2025-05-17', 1),
('Mi Monitor 2шт', 1, 3268000, 36, NULL, 1),
('Стол с тумбой, Кресло (ИНВ-М006)', 1, 3760000, 36, '2025-07-26', 1),
('Стол, Кресло (ИНВ-М041) (ing tili)', 1, 2632500, 36, '2024-08-02', 1),
('Стол, Кресло (ИНВ-М042) (ing tili)', 1, 2647900, 36, '2024-07-19', 1),
('Стол, Кресло (ИНВ-М043) (ing tili)', 1, 2647900, 36, '2024-07-19', 1),
('Стол, Кресло (ИНВ-М044) (ing tili)', 1, 2647900, 36, '2024-07-19', 1),
('Стол, Кресло (ИНВ-М050)', 1, 2700000, 36, '2025-07-26', 1),
('Стол, Кресло (ИНВ-М051)', 1, 2700000, 36, '2025-07-26', 1),
('Стол, Кресло (ИНВ-М052)', 1, 2700000, 36, '2025-07-26', 1),
('Кресло2шт', 1, 2700000, 36, '2025-07-26', 1),
('Кресло офисное ELIAN (ИНВ-М053)', 1, 1276250, 36, '2024-12-18', 1),
('Кресло офисное 8шт + Стол для заседаний 1шт Зал (ИНВ-М054)', 1, 12153120, 36, '2024-08-02', 1),
('Диван ММ 223.01.04', 1, 2146250, 36, NULL, 1);

COMMIT;

PRAGMA foreign_keys = ON;

-- ── Sanity checks (informational — run manually after the script) ───────────
-- SELECT COUNT(*) FROM personal_equipment;                          -- expect 137
-- SELECT COUNT(*) FROM personal_equipment WHERE staff_id IS NULL;   -- expect 37 (unmatched owners, see comments above)
-- SELECT COUNT(*) FROM general_equipment;                           -- expect 18
-- SELECT SUM(price) FROM personal_equipment;
-- SELECT SUM(quantity * price) FROM general_equipment;
-- Grand total of the two sums above should equal ~1598266575.99 UZS
-- (matches column L110 'Итого' in the source Excel sheet, ~1,598,266,576 UZS).
