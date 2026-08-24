# -*- coding: utf-8 -*-
"""Run the v4 rate engine against a fixture and print the rates as JSON.

Executed as a subprocess by test_rates.py. It has to be a separate process
because the v4 and v5 model packages are both named `models` and cannot be
imported into one interpreter.

    python _v4_rate_probe.py <fixture.json> <v4_root> <temp_db>
"""
import json
import os
import sys


def main():
    fixture_path, v4_root, db_path = sys.argv[1], sys.argv[2], sys.argv[3]
    fixture = json.load(open(fixture_path, encoding='utf-8'))

    if os.path.exists(db_path):
        os.remove(db_path)
    os.environ['MIZAN_ADMIN_PASSWORD'] = 'probe'

    sys.path.insert(0, v4_root)
    import models.base as v4base
    v4base.DB_PATH = db_path              # every v4 query reads this at call time
    v4base.init_db()

    from models.staff import calculate_hourly_rate

    conn = v4base.get_db()
    # v4 seeds a default overhead table on a fresh install; the fixture must be
    # the only source of overhead or the comparison is meaningless.
    conn.execute("DELETE FROM overhead")
    for key, value in fixture['settings'].items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))

    staff_ids = {}
    for s in fixture['staff']:
        cur = conn.execute(
            "INSERT INTO staff (name, role, department, staff_type, is_active)"
            " VALUES (?,?,?,?,1)",
            (s['name'], s['role'], s['department'], s['staff_type']))
        staff_ids[s['name']] = cur.lastrowid
        conn.execute(
            "INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
            " VALUES (?,?,?,?)",
            (cur.lastrowid, s['base_salary'], s['premium'], fixture['salary_start']))

    for e in fixture['personal_equipment']:
        conn.execute(
            "INSERT INTO personal_equipment (name, staff_id, price, lifespan_months,"
            " purchase_date, is_active) VALUES (?,?,?,?,?,1)",
            (e['name'], staff_ids[e['staff']], e['price'], e['lifespan_months'],
             e.get('purchase_date')))
    for e in fixture['general_equipment']:
        conn.execute(
            "INSERT INTO general_equipment (name, quantity, price, lifespan_months,"
            " purchase_date, is_active) VALUES (?,?,?,?,?,1)",
            (e['name'], e['quantity'], e['price'], e['lifespan_months'],
             e.get('purchase_date')))
    for l in fixture['licenses']:
        conn.execute(
            "INSERT INTO personal_licenses (name, staff_id, annual_cost, is_active)"
            " VALUES (?,?,?,1)", (l['name'], staff_ids[l['staff']], l['annual_cost']))
    for o in fixture['overhead']:
        conn.execute(
            "INSERT INTO overhead (name, monthly_amount, is_active) VALUES (?,?,1)",
            (o['name'], o['monthly_amount']))

    project_ids = {}
    for p in fixture['projects']:
        cur = conn.execute(
            "INSERT INTO projects (name, is_billable, status) VALUES (?,?,?)",
            (p['name'], p['is_billable'], 'active'))
        project_ids[p['name']] = cur.lastrowid
    for h in fixture['hours']:
        conn.execute(
            "INSERT INTO project_hours (project_id, staff_id, hours, period, source)"
            " VALUES (?,?,?,?,'manual')",
            (project_ids[h['project']], staff_ids[h['staff']], h['hours'], h['period']))
    conn.commit()
    conn.close()

    out = {}
    for s in fixture['staff']:
        if s['staff_type'] != 'production':
            continue
        info = calculate_hourly_rate(staff_ids[s['name']])
        out[s['name']] = {k: info[k] for k in (
            'base_salary', 'premium', 'tax', 'social', 'admin_share',
            'personal_eq', 'personal_lic', 'general_eq', 'overhead_share',
            'total_monthly', 'available_hours', 'cost_rate', 'billing_rate')}
    print(json.dumps(out))


if __name__ == '__main__':
    main()
