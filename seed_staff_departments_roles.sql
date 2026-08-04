-- =============================================================================
-- MIZAN Finance — reload staff / departments / staff_roles from
-- "По отделам соотрудники.xlsx" (Telegram Desktop export, received 2026-08-04)
--
-- Scope: staff, departments, staff_roles ONLY.
-- Assumes all tables that reference staff.id (salary_history, project_hours,
-- personal_equipment, personal_licenses, transactions.responsible_id,
-- milestone_staff, period_allocations, ...) have already been cleared by the
-- caller, so old staff rows can be dropped outright without FK cleanup here.
--
-- staff.department / staff.role are free-text columns (no FK to departments /
-- staff_roles — see models/base.py); they just need to match label_uz exactly
-- so the lookup-table dropdowns line up with what's stored on staff rows.
--
-- staff_code is intentionally left NULL: models/base.py → init_db() assigns
-- MZ-/MA- codes to any staff row with staff_code IS NULL on next app startup,
-- numbered by staff.id in insertion order below.
-- =============================================================================

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ── Drop old data ────────────────────────────────────────────────────────────
DELETE FROM staff;
DELETE FROM departments;
DELETE FROM staff_roles;
DELETE FROM sqlite_sequence WHERE name IN ('staff', 'departments', 'staff_roles');

-- ── departments (10 bo'lim) ──────────────────────────────────────────────────
INSERT INTO departments (code, label_uz, label_en, label_ru, is_active, sort_order) VALUES
('administrasiya',     'Administrasiya',       'Administration',             'Администрация',                       1, 10),
('texnik_hisob_kitob',  'Texnik hisob-kitob',   'Technical Accounting (PTO)', 'Производственно-технический отдел',   1, 20),
('grafik_dizayn',       'Grafik dizayn',        'Graphic Design',             'Графический дизайн',                  1, 30),
('master_plan',         'Master plan',          'Master Plan',                'Мастер-план',                          1, 40),
('bim',                 'BIM',                  'BIM',                        'BIM',                                  1, 50),
('ai_integratsiya',     'AI integratsiya',      'AI Integration',             'Интеграция ИИ',                        1, 60),
('vizualizatsiya',      'Vizualizatsiya',       'Visualization',              'Визуализация',                         1, 70),
('konstruksiya',        'Konstruksiya',         'Structural',                 'Конструкция',                          1, 80),
('elektrika',           'Elektrika',            'Electrical',                 'Электрика',                            1, 90),
('interyer',            'Interyer',             'Interior',                   'Интерьер',                             1, 100);

-- ── staff_roles (19 lavozim) ─────────────────────────────────────────────────
INSERT INTO staff_roles (code, label_uz, label_en, label_ru, is_active, sort_order) VALUES
('glavniy_direktor',     'Glavniy direktor',                                                   'General Director',                     'Главный директор',                       1, 10),
('direktor',             'Direktor',                                                           'Director',                              'Директор',                                1, 20),
('hr_menejer',           'HR menejer',                                                         'HR Manager',                            'HR-менеджер',                             1, 30),
('financer',             'Financer',                                                           'Finance Manager',                       'Финансист',                               1, 40),
('buhgalter',            'Buhgalter',                                                          'Accountant',                            'Бухгалтер',                               1, 50),
('ofis_menejeri',        'Ofis menejeri',                                                      'Office Manager',                        'Офис-менеджер',                           1, 60),
('farrosh',              'Farrosh',                                                            'Cleaner',                               'Уборщица',                                1, 70),
('taminotchi',           'Taminotchi',                                                         'Procurement Officer',                   'Снабженец',                               1, 80),
('pto',                  'PTO (Ishlab chiqarish-texnik hodim)',                                'Production & Technical Officer',        'Инженер ПТО',                             1, 90),
('grafik_dizayner',      'Grafik Dizayner',                                                    'Graphic Designer',                      'Графический дизайнер',                    1, 100),
('urban_designer',       'Urban Designer',                                                     'Urban Designer',                        'Урбан-дизайнер',                          1, 110),
('bosh_arxitektor',      'Bosh arxitektor',                                                    'Chief Architect',                       'Главный архитектор',                      1, 120),
('arxitektor',           'Arxitektor',                                                         'Architect',                             'Архитектор',                              1, 130),
('ai_direktor',          'Sun''iy intellekt strategiyasi va innovatsiyalar bo''yicha direktor', 'Director of AI Strategy & Innovation',  'Директор по стратегии ИИ и инновациям',   1, 140),
('tarjimon',             'Tarjimon',                                                           'Translator',                            'Переводчик',                              1, 150),
('visualizator',         'Visualizator',                                                       'Visualizer',                            'Визуализатор',                            1, 160),
('muxandis_konstruktor', 'Muxandis Konstruktor',                                               'Structural Engineer',                   'Инженер-конструктор',                     1, 170),
('muxandis_elektrik',    'Muxandis Elektrik',                                                  'Electrical Engineer',                   'Инженер-электрик',                        1, 180),
('interyer_dizayneri',   'Interyer dizayneri',                                                 'Interior Designer',                     'Дизайнер интерьера',                      1, 190);

-- ── staff (38 xodim) ──────────────────────────────────────────────────────────
-- staff_type: 'admin' = Administrasiya / Texnik hisob-kitob / AI integratsiya
-- departments (support/overhead roles, cost redistributed, no project hours);
-- 'production' = all other departments (bill hours to projects).
INSERT INTO staff (name, full_name, role, department, staff_type) VALUES
-- Administrasiya (admin)
('Jahongir',      'Ikromov Jahongir Kamolovich',            'Glavniy direktor',                    'Administrasiya',      'admin'),
('Behzod',        'Niyazov Behzod Xakimovich',               'Direktor',                             'Administrasiya',      'admin'),
('Umar R',        'Risbekov Umar Ag''zam o''g''li',           'HR menejer',                           'Administrasiya',      'admin'),
('Orifjon',       'Obidov Orifjon Yusupovich',                'Financer',                             'Administrasiya',      'admin'),
('Nurxayot',      'Abralov Nurxayot Ilyas o''g''li',          'Buhgalter',                            'Administrasiya',      'admin'),
('Maftuna',       'Odilova Maftuna',                          'Ofis menejeri',                        'Administrasiya',      'admin'),
('Nazokat',       'Nazokat Nurmatova',                        'Farrosh',                              'Administrasiya',      'admin'),
('Ubaydulloh',    'Ubaydulloh Yuldoshev',                     'Taminotchi',                           'Administrasiya',      'admin'),
-- Texnik hisob-kitob (admin)
('Ruslana',       'Halilova Ruslana Sitmerovna',              'PTO (Ishlab chiqarish-texnik hodim)',  'Texnik hisob-kitob',  'admin'),
-- Grafik dizayn (production)
('Adizjon',       'Saidov Adizjon Qaxramonovich',             'Grafik Dizayner',                      'Grafik dizayn',       'production'),
-- Master plan (production)
('Shohzod',       'Rahmatov Shohzod Karim o''g''li',          'Urban Designer',                       'Master plan',         'production'),
('Umidjon',       'Raxmatov Umidjon Mirfoziljon o''g''li',    'Urban Designer',                       'Master plan',         'production'),
('Mirolim',       'Mo''minov Mirolim Shuxratovich',           'Urban Designer',                       'Master plan',         'production'),
-- BIM (production)
('Aziz O',        'Omonov Aziz Farxod o''g''li',              'Bosh arxitektor',                      'BIM',                 'production'),
('Zafarjon',      'Rahmatov Zafarjon G''ayrat o''g''li',      'Arxitektor',                            'BIM',                 'production'),
('Ibrohim',       'Islomov Ibrohim Ilxomjon o''g''li',        'Arxitektor',                            'BIM',                 'production'),
('Xasan',         'G''anixo''jayev Xasan Saidumar o''g''li',  'Arxitektor',                            'BIM',                 'production'),
('Abror',         'Pirimqulov Abror Faxriddin o''g''li',      'Arxitektor',                            'BIM',                 'production'),
('Iskandar',      'Xudoyberdiyev Iskandar Bahromovich',       'Arxitektor',                            'BIM',                 'production'),
('Abdurashid',    'Abdug''ofurov Abdurashid Moxir o''g''li',  'Arxitektor',                            'BIM',                 'production'),
('Javoxir',       'Murodov Javoxir Avaz o''g''li',            'Arxitektor',                            'BIM',                 'production'),
-- AI integratsiya (admin)
('Davron',        'Djurayev Davron Batirovich',               'Sun''iy intellekt strategiyasi va innovatsiyalar bo''yicha direktor', 'AI integratsiya', 'admin'),
('Madina',        'Berdiyeva Madina Ganjiyevna',               'Tarjimon',                             'AI integratsiya',     'admin'),
-- Vizualizatsiya (production)
('Islom',         'Jo''rayev Islom Botirovich',                'Visualizator',                         'Vizualizatsiya',      'production'),
('Abdulla',       'Abdullayev Abdulla Mansurjonovich',         'Visualizator',                         'Vizualizatsiya',      'production'),
('Umar S',        'Sharipov Umar Ortiq o''g''li',              'Visualizator',                         'Vizualizatsiya',      'production'),
('Ramziddin',     'Muxutdinov Ramziddin Nizamitdinovich',      'Visualizator',                         'Vizualizatsiya',      'production'),
('Abdullo',       'Inag''omov Abdullo Baxtiyorovich',          'Visualizator',                         'Vizualizatsiya',      'production'),
('Shaxboz',       'Umataliyev Shaxboz Narimon o''g''li',       'Visualizator',                         'Vizualizatsiya',      'production'),
-- Konstruksiya (production)
('Zoirjon',       'Axmadaliyev Zoirjon Zohidjon o''g''li',     'Muxandis Konstruktor',                 'Konstruksiya',        'production'),
('Rovshan',       'Egamberdiyev Rovshan Abduqodir o''g''li',   'Muxandis Konstruktor',                 'Konstruksiya',        'production'),
('Shoxobiddin',   'Yuldashov Shoxobiddin Boynazar o''g''li',   'Muxandis Konstruktor',                 'Konstruksiya',        'production'),
-- Elektrika (production)
('Baxodir',       'Mamedov Baxodir Guychmatovich',             'Muxandis Elektrik',                    'Elektrika',           'production'),
('Atamurat',      'Kamalov Atamurat Raxatovich',                'Muxandis Elektrik',                    'Elektrika',           'production'),
('Abdulxafizxon', 'Saidov Abdulxafizxon Murodjon o''g''li',    'Muxandis Elektrik',                    'Elektrika',           'production'),
('Saidbek',       'Usmonaliyev Saidbek Jamoliddin o''g''li',   'Muxandis Elektrik',                    'Elektrika',           'production'),
-- Interyer (production)
('Aziz M',        'Muxamedov Aziz Sandjarovich',                'Interyer dizayneri',                   'Interyer',            'production'),
('Olchinbek',     'Olimov Olchinbek Olim o''g''li',             'Interyer dizayneri',                   'Interyer',            'production');

COMMIT;

PRAGMA foreign_keys = ON;

-- ── Sanity checks (informational — run manually after the script) ───────────
-- SELECT COUNT(*) AS total_staff FROM staff;                          -- expect 38
-- SELECT staff_type, COUNT(*) FROM staff GROUP BY staff_type;         -- expect admin=11, production=27
-- SELECT department, COUNT(*) FROM staff GROUP BY department ORDER BY department;
-- SELECT * FROM staff WHERE name NOT IN (SELECT name FROM staff);     -- always empty, uniqueness guard
