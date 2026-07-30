"""Reference data management: transaction_categories and counterparties."""
from flask import Blueprint, request
from auth import require_role
from utils import render_page
from models import get_db, apply_entity_resolution

bp = Blueprint('reference', __name__)


# ── Transaction Categories ─────────────────────────────────────────────────────

def _build_category_tree(rows):
    """Flatten categories into depth-annotated list: roots first, children indented."""
    by_parent = {}
    for r in rows:
        pid = r['parent_id']
        by_parent.setdefault(pid, []).append(dict(r))

    result = []

    def walk(pid, depth):
        for node in sorted(by_parent.get(pid, []), key=lambda x: (x['sort_order'], x['code'])):
            node['depth'] = depth
            result.append(node)
            walk(node['id'], depth + 1)

    walk(None, 0)

    # Orphans (parent deleted) — append at depth 0
    seen = {r['id'] for r in result}
    for r in rows:
        if r['id'] not in seen:
            d = dict(r)
            d['depth'] = 0
            result.append(d)
    return result


@bp.route('/reference/categories', methods=['GET', 'POST'])
@require_role('admin')
def categories_page():
    msg = ''
    if request.method == 'POST':
        msg = _handle_categories_post()

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transaction_categories ORDER BY sort_order, code"
    ).fetchall()
    conn.close()

    tree = _build_category_tree(rows)
    roots = [r for r in tree if r['parent_id'] is None]

    return render_page('categories', 'categories.html',
        tree=tree,
        roots=roots,
        msg=msg,
    )


def _handle_categories_post():
    action = request.form.get('action', '')
    conn = get_db()
    try:
        if action == 'add':
            code = request.form.get('code', '').strip().upper()
            name_uz = request.form.get('name_uz', '').strip()
            if not code or not name_uz:
                return '<div class="alert alert-warn">Kod va nomi (uz) majburiy.</div>'
            parent_id = request.form.get('parent_id') or None
            direction = request.form.get('direction', 'external')
            sort_order = int(request.form.get('sort_order') or 100)
            affects_cost = 1 if request.form.get('affects_project_cost') else 0
            affects_cf = 1 if request.form.get('affects_cash_flow') else 0
            name_en = request.form.get('name_en', '').strip() or None
            name_ru = request.form.get('name_ru', '').strip() or None
            notes = request.form.get('notes', '').strip() or None
            try:
                conn.execute('''INSERT INTO transaction_categories
                    (code, name_uz, name_en, name_ru, parent_id, direction,
                     affects_project_cost, affects_cash_flow, sort_order, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (code, name_uz, name_en, name_ru, parent_id, direction,
                     affects_cost, affects_cf, sort_order, notes))
                conn.commit()
                return f'<div class="alert alert-success">"{name_uz}" kategoriyasi qo\'shildi.</div>'
            except Exception as e:
                if 'UNIQUE' in str(e):
                    return f'<div class="alert alert-warn">{code} kodi allaqachon mavjud.</div>'
                return f'<div class="alert alert-warn">Xato: {e}</div>'

        elif action == 'edit':
            cat_id = int(request.form.get('id', 0))
            code = request.form.get('code', '').strip().upper()
            name_uz = request.form.get('name_uz', '').strip()
            if not cat_id or not code or not name_uz:
                return '<div class="alert alert-warn">Majburiy maydonlar to\'ldirilmagan.</div>'
            parent_id = request.form.get('parent_id') or None
            if parent_id and int(parent_id) == cat_id:
                parent_id = None
            direction = request.form.get('direction', 'external')
            sort_order = int(request.form.get('sort_order') or 100)
            affects_cost = 1 if request.form.get('affects_project_cost') else 0
            affects_cf = 1 if request.form.get('affects_cash_flow') else 0
            is_active = 1 if request.form.get('is_active') else 0
            name_en = request.form.get('name_en', '').strip() or None
            name_ru = request.form.get('name_ru', '').strip() or None
            notes = request.form.get('notes', '').strip() or None
            try:
                conn.execute('''UPDATE transaction_categories SET
                    code=?, name_uz=?, name_en=?, name_ru=?, parent_id=?,
                    direction=?, affects_project_cost=?, affects_cash_flow=?,
                    sort_order=?, is_active=?, notes=?
                    WHERE id=?''',
                    (code, name_uz, name_en, name_ru, parent_id, direction,
                     affects_cost, affects_cf, sort_order, is_active, notes, cat_id))
                conn.commit()
                return '<div class="alert alert-success">Kategoriya yangilandi.</div>'
            except Exception as e:
                if 'UNIQUE' in str(e):
                    return f'<div class="alert alert-warn">{code} kodi allaqachon mavjud.</div>'
                return f'<div class="alert alert-warn">Xato: {e}</div>'

        elif action == 'delete':
            cat_id = int(request.form.get('id', 0))
            used = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE category_id=?", (cat_id,)
            ).fetchone()[0]
            if used:
                return f'<div class="alert alert-warn">Bu kategoriya {used} ta yozuvda ishlatilmoqda — o\'chirib bo\'lmaydi.</div>'
            children = conn.execute(
                "SELECT COUNT(*) FROM transaction_categories WHERE parent_id=?", (cat_id,)
            ).fetchone()[0]
            if children:
                return f'<div class="alert alert-warn">Avval {children} ta ichki kategoriyani o\'chiring.</div>'
            conn.execute("DELETE FROM transaction_categories WHERE id=?", (cat_id,))
            conn.commit()
            return '<div class="alert alert-success">Kategoriya o\'chirildi.</div>'

    finally:
        conn.close()
    return ''


# ── Counterparties ─────────────────────────────────────────────────────────────

@bp.route('/reference/counterparties', methods=['GET', 'POST'])
@require_role('admin')
def counterparties_page():
    msg = ''
    if request.method == 'POST':
        msg = _handle_counterparties_post()

    conn = get_db()
    cps = conn.execute("SELECT * FROM counterparties ORDER BY name").fetchall()
    aliases = conn.execute(
        "SELECT * FROM counterparty_aliases ORDER BY alias"
    ).fetchall()
    conn.close()

    alias_map = {}
    for a in aliases:
        alias_map.setdefault(a['counterparty_id'], []).append(dict(a))

    cp_list = []
    for cp in cps:
        d = dict(cp)
        d['aliases'] = alias_map.get(cp['id'], [])
        cp_list.append(d)

    return render_page('counterparties', 'counterparties.html',
        cp_list=cp_list,
        msg=msg,
    )


def _handle_counterparties_post():
    action = request.form.get('action', '')
    conn = get_db()
    try:
        if action == 'add_cp':
            name = request.form.get('name', '').strip()
            if not name:
                return '<div class="alert alert-warn">Nomi majburiy.</div>'
            legal_name = request.form.get('legal_name', '').strip() or None
            tax_id = request.form.get('tax_id', '').strip() or None
            cp_type = request.form.get('counterparty_type', 'other')
            country = request.form.get('country', '').strip() or None
            currency = request.form.get('default_currency', 'UZS')
            notes = request.form.get('notes', '').strip() or None
            conn.execute('''INSERT INTO counterparties
                (name, legal_name, tax_id, counterparty_type, country, default_currency, notes)
                VALUES (?,?,?,?,?,?,?)''',
                (name, legal_name, tax_id, cp_type, country, currency, notes))
            conn.commit()
            return f'<div class="alert alert-success">"{name}" qo\'shildi.</div>'

        elif action == 'edit_cp':
            cp_id = int(request.form.get('id', 0))
            name = request.form.get('name', '').strip()
            if not cp_id or not name:
                return '<div class="alert alert-warn">Nomi majburiy.</div>'
            legal_name = request.form.get('legal_name', '').strip() or None
            tax_id = request.form.get('tax_id', '').strip() or None
            cp_type = request.form.get('counterparty_type', 'other')
            country = request.form.get('country', '').strip() or None
            currency = request.form.get('default_currency', 'UZS')
            is_active = 1 if request.form.get('is_active') else 0
            notes = request.form.get('notes', '').strip() or None
            conn.execute('''UPDATE counterparties SET
                name=?, legal_name=?, tax_id=?, counterparty_type=?,
                country=?, default_currency=?, is_active=?, notes=?
                WHERE id=?''',
                (name, legal_name, tax_id, cp_type, country, currency, is_active, notes, cp_id))
            conn.commit()
            return '<div class="alert alert-success">Kontragent yangilandi.</div>'

        elif action == 'delete_cp':
            cp_id = int(request.form.get('id', 0))
            used = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE counterparty_id=?", (cp_id,)
            ).fetchone()[0]
            if used:
                return f'<div class="alert alert-warn">Bu kontragent {used} ta tranzaktsiyada ishlatilmoqda — o\'chirib bo\'lmaydi.</div>'
            conn.execute("DELETE FROM counterparty_aliases WHERE counterparty_id=?", (cp_id,))
            conn.execute("DELETE FROM counterparties WHERE id=?", (cp_id,))
            conn.commit()
            return '<div class="alert alert-success">Kontragent o\'chirildi.</div>'

        elif action == 'add_alias':
            cp_id = int(request.form.get('counterparty_id', 0))
            alias = request.form.get('alias', '').strip()
            if not cp_id or not alias:
                return '<div class="alert alert-warn">Taxallus matni majburiy.</div>'
            source = request.form.get('source', '').strip() or 'manual'
            try:
                conn.execute(
                    "INSERT INTO counterparty_aliases (counterparty_id, alias, source) VALUES (?,?,?)",
                    (cp_id, alias, source)
                )
                conn.commit()
                return f'<div class="alert alert-success">"{alias}" taxallussi qo\'shildi.</div>'
            except Exception as e:
                if 'UNIQUE' in str(e):
                    return '<div class="alert alert-warn">Bu taxallus allaqachon mavjud.</div>'
                return f'<div class="alert alert-warn">Xato: {e}</div>'

        elif action == 'delete_alias':
            alias_id = int(request.form.get('alias_id', 0))
            conn.execute("DELETE FROM counterparty_aliases WHERE id=?", (alias_id,))
            conn.commit()
            return '<div class="alert alert-success">Taxallus o\'chirildi.</div>'

    finally:
        conn.close()
    return ''


# ── Import reconciliation (unmatched project/counterparty names) ──────────────

@bp.route('/reference/reconcile', methods=['GET', 'POST'])
@require_role('admin')
def reconcile_page():
    msg = ''
    if request.method == 'POST':
        msg = _handle_reconcile_post()

    conn = get_db()
    unresolved = conn.execute('''
        SELECT entity_type, raw_name, COUNT(*) AS cnt
        FROM unresolved_imports WHERE resolved=0
        GROUP BY entity_type, raw_name
        ORDER BY cnt DESC, raw_name
    ''').fetchall()
    projects = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
    counterparties = conn.execute("SELECT id, name FROM counterparties ORDER BY name").fetchall()
    conn.close()

    project_unresolved = [dict(r) for r in unresolved if r['entity_type'] == 'project']
    cp_unresolved = [dict(r) for r in unresolved if r['entity_type'] == 'counterparty']

    return render_page('reconcile', 'reconcile.html',
        project_unresolved=project_unresolved,
        cp_unresolved=cp_unresolved,
        projects=projects,
        counterparties=counterparties,
        msg=msg,
    )


def _handle_reconcile_post():
    action = request.form.get('action', '')
    entity_type = request.form.get('entity_type', '')
    raw_name = request.form.get('raw_name', '')
    if entity_type not in ('project', 'counterparty') or not raw_name:
        return '<div class="alert alert-warn">Noto\'g\'ri so\'rov.</div>'

    label = 'loyiha' if entity_type == 'project' else 'kontragent'
    conn = get_db()
    try:
        if action == 'link':
            target_id = int(request.form.get('target_id', 0) or 0)
            if not target_id:
                return f'<div class="alert alert-warn">Mavjud {label}ni tanlang.</div>'
            apply_entity_resolution(conn, entity_type, raw_name, target_id)
            return f'<div class="alert alert-success">"{raw_name}" mavjud {label}ga bog\'landi.</div>'

        elif action == 'create_new':
            table = 'projects' if entity_type == 'project' else 'counterparties'
            new_name = request.form.get('new_name', '').strip() or raw_name
            try:
                cur = conn.execute(f"INSERT INTO {table} (name) VALUES (?)", (new_name,))
                new_id = cur.lastrowid
            except Exception as e:
                if 'UNIQUE' in str(e):
                    existing = conn.execute(f"SELECT id FROM {table} WHERE name=?", (new_name,)).fetchone()
                    new_id = existing['id'] if existing else None
                else:
                    return f'<div class="alert alert-warn">Xato: {e}</div>'
            if not new_id:
                return f'<div class="alert alert-warn">{label.capitalize()} yaratilmadi.</div>'
            apply_entity_resolution(conn, entity_type, raw_name, new_id)
            return f'<div class="alert alert-success">"{new_name}" yangi {label} sifatida yaratildi va bog\'landi.</div>'

        elif action == 'ignore':
            conn.execute(
                "UPDATE unresolved_imports SET resolved=1 WHERE entity_type=? AND raw_name=?",
                (entity_type, raw_name)
            )
            conn.commit()
            return f'<div class="alert alert-success">"{raw_name}" e\'tiborsiz qoldirildi.</div>'

    finally:
        conn.close()
    return ''


# ── Lookup Tables (tx_types & payment_types) ──────────────────────────────────

@bp.route('/reference/lookup-tables', methods=['GET', 'POST'])
@require_role('admin')
def lookup_tables_page():
    msg = ''
    if request.method == 'POST':
        msg = _handle_lookup_post()

    conn = get_db()
    tx_types = conn.execute("SELECT * FROM tx_types ORDER BY sort_order, code").fetchall()
    payment_types = conn.execute("SELECT * FROM payment_types ORDER BY sort_order, code").fetchall()
    work_types = conn.execute("SELECT * FROM work_types ORDER BY sort_order, code").fetchall()
    departments = conn.execute("SELECT * FROM departments ORDER BY sort_order, code").fetchall()
    staff_roles = conn.execute("SELECT * FROM staff_roles ORDER BY sort_order, code").fetchall()
    conn.close()

    return render_page('lookup_tables', 'lookup_tables.html',
        tx_types=tx_types,
        payment_types=payment_types,
        work_types=work_types,
        departments=departments,
        staff_roles=staff_roles,
        msg=msg,
    )


def _handle_lookup_post():
    action = request.form.get('action', '')
    table = request.form.get('table', '')
    if table not in ('tx_types', 'payment_types', 'work_types', 'departments', 'staff_roles'):
        return '<div class="alert alert-warn">Noto\'g\'ri jadval.</div>'

    conn = get_db()
    try:
        if action == 'add':
            code = request.form.get('code', '').strip().lower().replace(' ', '_')
            label_uz = request.form.get('label_uz', '').strip()
            if not code or not label_uz:
                return '<div class="alert alert-warn">Kod va nomi majburiy.</div>'
            label_en = request.form.get('label_en', '').strip() or None
            label_ru = request.form.get('label_ru', '').strip() or None
            sort_order = int(request.form.get('sort_order') or 100)
            extra_col = ', direction' if table == 'tx_types' else ''
            extra_val = ', ?' if table == 'tx_types' else ''
            params = [code, label_uz, label_en, label_ru, sort_order]
            if table == 'tx_types':
                direction = request.form.get('direction', 'external')
                params.append(direction)
                try:
                    conn.execute(
                        f"INSERT INTO tx_types (code, label_uz, label_en, label_ru, sort_order, direction)"
                        f" VALUES (?,?,?,?,?,?)", params
                    )
                except Exception as e:
                    if 'UNIQUE' in str(e):
                        return f'<div class="alert alert-warn">{code} kodi allaqachon mavjud.</div>'
                    return f'<div class="alert alert-warn">Xato: {e}</div>'
            else:
                try:
                    conn.execute(
                        f"INSERT INTO {table} (code, label_uz, label_en, label_ru, sort_order)"
                        " VALUES (?,?,?,?,?)", params
                    )
                except Exception as e:
                    if 'UNIQUE' in str(e):
                        return f'<div class="alert alert-warn">{code} kodi allaqachon mavjud.</div>'
                    return f'<div class="alert alert-warn">Xato: {e}</div>'
            conn.commit()
            return f'<div class="alert alert-success">"{label_uz}" qo\'shildi.</div>'

        elif action == 'edit':
            row_id = int(request.form.get('id', 0))
            code = request.form.get('code', '').strip().lower().replace(' ', '_')
            label_uz = request.form.get('label_uz', '').strip()
            if not row_id or not code or not label_uz:
                return '<div class="alert alert-warn">Majburiy maydonlar to\'ldirilmagan.</div>'
            label_en = request.form.get('label_en', '').strip() or None
            label_ru = request.form.get('label_ru', '').strip() or None
            sort_order = int(request.form.get('sort_order') or 100)
            is_active = 1 if request.form.get('is_active') else 0
            try:
                if table == 'tx_types':
                    direction = request.form.get('direction', 'external')
                    conn.execute(
                        "UPDATE tx_types SET code=?, label_uz=?, label_en=?, label_ru=?,"
                        " sort_order=?, is_active=?, direction=? WHERE id=?",
                        (code, label_uz, label_en, label_ru, sort_order, is_active, direction, row_id)
                    )
                else:
                    # staff.department / staff.role store the label_uz value, so a
                    # rename must be carried over or those rows go stale.
                    staff_col = {'departments': 'department', 'staff_roles': 'role'}.get(table)
                    old_label = None
                    if staff_col:
                        prev = conn.execute(
                            f"SELECT label_uz FROM {table} WHERE id=?", (row_id,)
                        ).fetchone()
                        old_label = prev['label_uz'] if prev else None
                    conn.execute(
                        f"UPDATE {table} SET code=?, label_uz=?, label_en=?, label_ru=?,"
                        " sort_order=?, is_active=? WHERE id=?",
                        (code, label_uz, label_en, label_ru, sort_order, is_active, row_id)
                    )
                    if staff_col and old_label and old_label != label_uz:
                        conn.execute(
                            f"UPDATE staff SET {staff_col}=? WHERE {staff_col}=?",
                            (label_uz, old_label)
                        )
            except Exception as e:
                if 'UNIQUE' in str(e):
                    return f'<div class="alert alert-warn">{code} kodi allaqachon mavjud.</div>'
                return f'<div class="alert alert-warn">Xato: {e}</div>'
            conn.commit()
            return '<div class="alert alert-success">Yangilandi.</div>'

        elif action == 'delete':
            row_id = int(request.form.get('id', 0))
            if table == 'tx_types':
                used = conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE tx_type=("
                    "SELECT code FROM tx_types WHERE id=?)", (row_id,)
                ).fetchone()[0]
            elif table == 'work_types':
                used = conn.execute(
                    "SELECT COUNT(*) FROM project_phases WHERE work_type=("
                    "SELECT code FROM work_types WHERE id=?)", (row_id,)
                ).fetchone()[0]
            elif table == 'departments':
                used = conn.execute(
                    "SELECT COUNT(*) FROM staff WHERE department=("
                    "SELECT label_uz FROM departments WHERE id=?)", (row_id,)
                ).fetchone()[0]
            elif table == 'staff_roles':
                used = conn.execute(
                    "SELECT COUNT(*) FROM staff WHERE role=("
                    "SELECT label_uz FROM staff_roles WHERE id=?)", (row_id,)
                ).fetchone()[0]
            else:
                used = conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE payment_type=("
                    "SELECT code FROM payment_types WHERE id=?)", (row_id,)
                ).fetchone()[0]
            if used:
                return f'<div class="alert alert-warn">Bu qiymat {used} ta yozuvda ishlatilmoqda — o\'chirib bo\'lmaydi.</div>'
            conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
            conn.commit()
            return '<div class="alert alert-success">O\'chirildi.</div>'

    finally:
        conn.close()
    return ''
