"""Accounting (universal data entry) routes — one route per data-entry area."""
from datetime import datetime
from flask import Blueprint, request, redirect, flash
from auth import require_role
from utils import render_page, t, fmt
from models import get_db, get_rate_for_date, get_all_loans

bp = Blueprint('accounting', __name__)


def _derive_status(amount, paid):
    """Canonical status from committed vs received: paid / partial / pending."""
    if amount > 0 and paid >= amount:
        return 'paid'
    if paid > 0:
        return 'partial'
    return 'pending'


def _upsert_salary(conn, staff_id, salary, premium, start_date):
    """Insert a new salary_history row effective start_date, closing the prior
    open row. If a row already starts on start_date (e.g. a same-day correction),
    update it in place instead of inserting a duplicate — start_date is UNIQUE
    per staff_id."""
    existing = conn.execute(
        "SELECT id FROM salary_history WHERE staff_id=? AND start_date=?",
        (staff_id, start_date)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE salary_history SET base_salary=?, premium=? WHERE id=?",
            (salary, premium, existing['id'])
        )
    else:
        conn.execute(
            "UPDATE salary_history SET end_date=? WHERE staff_id=? AND end_date IS NULL",
            (start_date, staff_id)
        )
        conn.execute(
            "INSERT INTO salary_history (staff_id, base_salary, premium, start_date) VALUES (?,?,?,?)",
            (staff_id, salary, premium, start_date)
        )


@bp.route('/accounting')
@require_role('manager', 'admin')
def accounting_redirect():
    return redirect('/accounting/transactions')


@bp.route('/accounting/transactions', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_transactions():
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        conn = get_db()
        try:
            tx_date = request.form.get('date', today)
            direction = request.form.get('direction', 'kirish')
            tx_type = request.form.get('tx_type', '')
            desc = request.form.get('desc', '')
            paid_val = float(request.form.get('paid_val', 0) or 0)
            # committed amount; when omitted, treat paid as the committed amount
            amount_val = float(request.form.get('amount_val', 0) or 0) or paid_val
            contract_val = float(request.form.get('contract_val', 0) or 0)
            currency = request.form.get('currency', 'UZS')
            tx_exchange_rate = get_rate_for_date(tx_date)
            if currency == 'USD':
                amount_usd = amount_val
                amount_uzs = round(amount_val * tx_exchange_rate, 0)
                paid_uzs = round(paid_val * tx_exchange_rate, 0)
                contract_usd = contract_val
                contract_uzs = round(contract_val * tx_exchange_rate, 0)
            else:
                amount_uzs = amount_val
                amount_usd = round(amount_val / tx_exchange_rate, 2) if amount_val > 0 else 0
                paid_uzs = paid_val
                contract_uzs = contract_val
                contract_usd = round(contract_val / tx_exchange_rate, 2) if contract_val > 0 else 0
            responsible = request.form.get('responsible', '')
            paid_to = request.form.get('paid_to', '')
            client = request.form.get('client', '')
            doc_id = request.form.get('doc_id', '')
            payment_type = request.form.get('payment_type', 'bank')
            deadline = request.form.get('deadline', '')
            notes = request.form.get('notes', '')
            project_name = request.form.get('project', '')
            raw_cat = request.form.get('category_id', '')
            category_id = int(raw_cat) if raw_cat.isdigit() else None

            msg = ''
            # An invoice may be recorded before any money moves (paid = 0), as long
            # as a committed amount exists — that is what feeds AR aging.
            if (paid_val > 0 or amount_val > 0) and tx_type:
                if direction == 'tashqi':
                    project_id = None
                    if project_name:
                        proj = conn.execute(
                            "SELECT id FROM projects WHERE name=?", (project_name,)
                        ).fetchone()
                        if proj:
                            project_id = proj['id']
                    status = _derive_status(amount_uzs, paid_uzs)
                    conn.execute('''INSERT INTO transactions
                        (direction, tx_type, date, ref_id, doc_id, project_id, category_id,
                         description, client, responsible, paid_to,
                         contract_amount, contract_amount_usd, amount, amount_usd, paid,
                         currency, exchange_rate, payment_type, deadline, notes, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        ('external', tx_type, tx_date, f"PRJ-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                         doc_id or None, project_id, category_id, desc,
                         client or None, responsible or None, paid_to or None,
                         contract_uzs, contract_usd, amount_uzs, amount_usd,
                         paid_uzs, currency, tx_exchange_rate, payment_type, deadline or None, notes, status))
                    msg = f'<div class="alert alert-success">{t("acc_saved_external")}</div>'
                else:
                    conn.execute('''INSERT INTO transactions
                        (direction, tx_type, date, ref_id, doc_id, category_id, description,
                         responsible, paid_to,
                         contract_amount, contract_amount_usd, amount, amount_usd, paid,
                         currency, exchange_rate, payment_type, notes, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        ('internal', tx_type, tx_date, f"MZ-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                         doc_id or None, category_id, desc,
                         responsible or None, paid_to or None,
                         contract_uzs, contract_usd, amount_uzs, amount_usd,
                         paid_uzs, currency, tx_exchange_rate, payment_type, notes,
                         _derive_status(amount_uzs, paid_uzs)))
                    msg = f'<div class="alert alert-success">{t("acc_saved_internal")}</div>'

            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        sub = 'ext' if direction == 'tashqi' else 'int'
        return redirect(f'/accounting/transactions?sub={sub}')

    active_sub = request.args.get('sub', 'ext')
    if active_sub not in ('ext', 'int'):
        active_sub = 'ext'

    conn = get_db()
    projects = conn.execute("SELECT name FROM projects ORDER BY name").fetchall()
    tx_types_ext = conn.execute(
        "SELECT code, label_uz FROM tx_types WHERE direction IN ('external','both') AND is_active=1 ORDER BY sort_order"
    ).fetchall()
    tx_types_int = conn.execute(
        "SELECT code, label_uz FROM tx_types WHERE direction IN ('internal','both') AND is_active=1 ORDER BY sort_order"
    ).fetchall()
    payment_types = conn.execute(
        "SELECT code, label_uz FROM payment_types WHERE is_active=1 ORDER BY sort_order"
    ).fetchall()
    cats_ext = conn.execute(
        "SELECT id, name_uz FROM transaction_categories"
        " WHERE direction IN ('in','external','both') AND is_active=1 ORDER BY sort_order, name_uz"
    ).fetchall()
    cats_int = conn.execute(
        "SELECT id, name_uz FROM transaction_categories"
        " WHERE direction IN ('out','internal','both') AND is_active=1 ORDER BY sort_order, name_uz"
    ).fetchall()
    conn.close()

    return render_page('accounting_transactions', 'accounting_transactions.html',
        today_str=today,
        projects=projects,
        tx_types_ext=tx_types_ext,
        tx_types_int=tx_types_int,
        payment_types=payment_types,
        cats_ext=cats_ext,
        cats_int=cats_int,
        active_sub=active_sub,
    )


@bp.route('/accounting/projects', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_projects():
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            proj_name = request.form.get('proj_name', '').strip()
            if proj_name:
                conn.execute('''INSERT OR IGNORE INTO projects
                    (name, client, responsible, contract_amount, currency, start_date, end_date,
                     estimated_total_hours, risk_coefficient, status, is_billable)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1)''',
                    (proj_name, request.form.get('proj_client', ''),
                     request.form.get('proj_responsible', ''),
                     float(request.form.get('proj_contract', 0) or 0),
                     request.form.get('proj_currency', 'UZS'),
                     request.form.get('proj_start', today),
                     request.form.get('proj_end', '') or None,
                     float(request.form.get('proj_est_hours', 0) or 0),
                     float(request.form.get('proj_risk', 1.15) or 1.15),
                     request.form.get('proj_status', 'active')))
                msg = f"<div class=\"alert alert-success\">Proekt qo'shildi: {proj_name}</div>"
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/projects')

    return render_page('accounting_projects', 'accounting_projects.html',
        today_str=today,
    )


@bp.route('/accounting/staff', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_staff():
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            action = request.form.get('staff_action', 'update_salary')
            if action == 'add_staff':
                name = request.form.get('staff_name', '').strip()
                salary = float(request.form.get('staff_salary', 0) or 0)
                premium = float(request.form.get('staff_premium', 0) or 0)
                if name:
                    conn.execute(
                        "INSERT OR IGNORE INTO staff (name, role, department, staff_type) VALUES (?,?,?,?)",
                        (name, request.form.get('staff_role', ''),
                         request.form.get('staff_dept', ''), request.form.get('staff_type', 'production'))
                    )
                    conn.commit()
                    staff = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()
                    if staff and salary > 0:
                        _upsert_salary(conn, staff['id'], salary, premium, today)
                    msg = f'<div class="alert alert-success">{t("acc_staff_added", name)}</div>'
            elif action == 'update_salary':
                staff_id = int(request.form.get('staff_id', 0))
                salary = float(request.form.get('new_salary', 0) or 0)
                premium = float(request.form.get('new_premium', 0) or 0)
                if staff_id and salary > 0:
                    _upsert_salary(conn, staff_id, salary, premium, today)
                    msg = f'<div class="alert alert-success">{t("acc_salary_updated")}</div>'
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/staff')

    conn = get_db()
    staff_list = conn.execute(
        "SELECT id, name, role, staff_type FROM staff WHERE is_active=1 ORDER BY name"
    ).fetchall()
    staff_roles = conn.execute(
        "SELECT label_uz FROM staff_roles WHERE is_active=1 ORDER BY sort_order, label_uz"
    ).fetchall()
    departments = conn.execute(
        "SELECT label_uz FROM departments WHERE is_active=1 ORDER BY sort_order, label_uz"
    ).fetchall()
    conn.close()

    return render_page('accounting_staff', 'accounting_staff.html',
        staff_list=staff_list,
        staff_roles=staff_roles,
        departments=departments,
    )


@bp.route('/accounting/equipment', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_equipment():
    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            eq_name = request.form.get('eq_name', '').strip()
            eq_price = float(request.form.get('eq_price', 0) or 0)
            eq_months = int(request.form.get('eq_months', 36) or 36)
            eq_type = request.form.get('eq_type', 'personal')
            if eq_name and eq_price > 0:
                if eq_type == 'personal':
                    staff_id = int(request.form.get('eq_staff_id', 0) or 0)
                    conn.execute(
                        "INSERT INTO personal_equipment (name, staff_id, price, lifespan_months) VALUES (?,?,?,?)",
                        (eq_name, staff_id if staff_id else None, eq_price, eq_months)
                    )
                    msg = f'<div class="alert alert-success">{t("acc_eq_personal_added", eq_name)}</div>'
                else:
                    qty = int(request.form.get('eq_qty', 1) or 1)
                    conn.execute(
                        "INSERT INTO general_equipment (name, quantity, price, lifespan_months) VALUES (?,?,?,?)",
                        (eq_name, qty, eq_price, eq_months)
                    )
                    msg = f'<div class="alert alert-success">{t("acc_eq_general_added", eq_name)}</div>'
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/equipment')

    conn = get_db()
    staff_list = conn.execute(
        "SELECT id, name, role, staff_type FROM staff WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()
    prod_staff = [s for s in staff_list if s['staff_type'] == 'production']

    return render_page('accounting_equipment', 'accounting_equipment.html',
        prod_staff=prod_staff,
    )


@bp.route('/accounting/licenses', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_licenses():
    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            lic_name = request.form.get('lic_name', '').strip()
            lic_annual = float(request.form.get('lic_annual', 0) or 0)
            lic_staff_id = int(request.form.get('lic_staff_id', 0) or 0)
            if lic_name and lic_annual > 0:
                conn.execute(
                    "INSERT INTO personal_licenses (name, staff_id, annual_cost, license_type) VALUES (?,?,?,?)",
                    (lic_name, lic_staff_id if lic_staff_id else None, lic_annual,
                     request.form.get('lic_type', 'named'))
                )
                msg = f'<div class="alert alert-success">{t("acc_lic_added", lic_name)}</div>'
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/licenses')

    conn = get_db()
    staff_list = conn.execute(
        "SELECT id, name, role, staff_type FROM staff WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()
    prod_staff = [s for s in staff_list if s['staff_type'] == 'production']

    return render_page('accounting_licenses', 'accounting_licenses.html',
        prod_staff=prod_staff,
    )


@bp.route('/accounting/overhead', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_overhead():
    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            oh_action = request.form.get('oh_action', 'add')
            if oh_action == 'add':
                oh_name = request.form.get('oh_name', '').strip()
                oh_amount = float(request.form.get('oh_amount', 0) or 0)
                if oh_name and oh_amount > 0:
                    conn.execute(
                        "INSERT OR REPLACE INTO overhead (name, monthly_amount) VALUES (?,?)",
                        (oh_name, oh_amount)
                    )
                    msg = f'<div class="alert alert-success">{t("acc_oh_added", oh_name)}</div>'
            elif oh_action == 'update':
                oh_id = int(request.form.get('oh_id', 0) or 0)
                oh_amount = float(request.form.get('oh_new_amount', 0) or 0)
                if oh_id and oh_amount > 0:
                    conn.execute("UPDATE overhead SET monthly_amount=? WHERE id=?", (oh_amount, oh_id))
                    msg = f'<div class="alert alert-success">{t("acc_oh_updated")}</div>'
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/overhead')

    conn = get_db()
    overhead_list = conn.execute(
        "SELECT id, name, monthly_amount FROM overhead WHERE is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()

    return render_page('accounting_overhead', 'accounting_overhead.html',
        overhead_list=overhead_list,
    )


@bp.route('/accounting/loans', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def accounting_loans():
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        conn = get_db()
        msg = ''
        try:
            loan_action = request.form.get('loan_action', 'add_loan')
            if loan_action == 'add_loan':
                counterparty = request.form.get('counterparty', '').strip()
                total_amount = float(request.form.get('loan_amount', 0) or 0)
                currency = request.form.get('loan_currency', 'UZS')
                if counterparty and total_amount > 0:
                    conn.execute('''INSERT INTO loans
                        (loan_type, counterparty, description, total_amount, currency,
                         interest_rate, issue_date, due_date, notes)
                        VALUES (?,?,?,?,?,?,?,?,?)''',
                        (request.form.get('loan_type', 'olgan'), counterparty,
                         request.form.get('loan_desc', ''), total_amount, currency,
                         float(request.form.get('interest_rate', 0) or 0),
                         request.form.get('loan_date', today),
                         request.form.get('loan_due', '') or None,
                         request.form.get('loan_notes', '')))
                    msg = f'<div class="alert alert-success">{t("loans_msg_added", counterparty, fmt(total_amount), currency)}</div>'
            elif loan_action == 'add_payment':
                loan_id = int(request.form.get('payment_loan_id', 0))
                pay_amount = float(request.form.get('payment_amount', 0) or 0)
                if loan_id and pay_amount > 0:
                    pay_date = request.form.get('payment_date', today)
                    pay_type = request.form.get('payment_type', 'asosiy')
                    if pay_type not in ('asosiy', 'foiz'):
                        pay_type = 'asosiy'
                    conn.execute(
                        "INSERT INTO loan_payments (loan_id, date, amount, payment_type, notes) VALUES (?,?,?,?,?)",
                        (loan_id, pay_date, pay_amount, pay_type, request.form.get('payment_notes', ''))
                    )
                    loan = conn.execute(
                        "SELECT total_amount, currency FROM loans WHERE id=?", (loan_id,)
                    ).fetchone()
                    # Only principal payments count toward closing the loan —
                    # interest alone must never mark the principal as repaid.
                    paid_total = conn.execute(
                        "SELECT COALESCE(SUM(amount),0) as s FROM loan_payments"
                        " WHERE loan_id=? AND COALESCE(payment_type,'asosiy') != 'foiz'",
                        (loan_id,)
                    ).fetchone()['s']
                    if loan and paid_total >= loan['total_amount']:
                        conn.execute("UPDATE loans SET status='yopilgan' WHERE id=?", (loan_id,))
                    cur_label = loan['currency'] if loan else 'UZS'
                    msg = f'<div class="alert alert-success">{t("loans_msg_payment", fmt(pay_amount), cur_label)}</div>'
            conn.commit()
        finally:
            conn.close()

        if msg:
            flash(msg, 'success')
        return redirect('/accounting/loans')

    open_loans = [l for l in get_all_loans() if l['status'] == 'ochiq']

    return render_page('accounting_loans', 'accounting_loans.html',
        today_str=today,
        open_loans=open_loans,
    )
