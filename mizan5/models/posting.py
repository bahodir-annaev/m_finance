"""Document → journal entry rules. The only builder of postings in the app.

Each rule turns one document into a list of balanced lines for
ledger.post_entry(). Rules reference accounts by *purpose* (account_map), never
by hardcoded code, so re-pointing a purpose is a settings change rather than a
code change.

Every amount here is UZS. A foreign-currency document was already converted at
its document-date rate when it was saved; the original figures ride along on
the line as currency / amount_cur / exchange_rate for the audit trail.
"""
from .ledger import PostingError, account_id_for


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def cash_account_purpose(doc):
    """Which money account a cash document moves.

    Foreign currency lands on the FX account (5210) regardless of method: a USD
    receipt is not a so'm bank balance, and merging them would make the
    month-end revaluation impossible.
    """
    if (doc.get('currency') or 'UZS') != 'UZS':
        return 'cash_fx'
    if (doc.get('payment_method') or '') == 'naqd':
        return 'cash_till'
    return 'cash_bank'


def cash_account_for(conn, doc):
    """(money account id, bank_account_id) for a cash document.

    A document that names one of the firm's bank accounts moves *that*
    account's ledger account — a USD bank account is registered against 5210,
    so the currency routing is explicit rather than inferred. Without one the
    purpose rule above decides and the analytic stays empty; post_entry() then
    refuses the line if the ledger account has bank accounts registered.
    """
    bank_id = doc.get('bank_account_id')
    if not bank_id:
        return account_id_for(cash_account_purpose(doc), conn), None
    bank = conn.execute("SELECT * FROM bank_accounts WHERE id=?", (bank_id,)).fetchone()
    if not bank:
        raise PostingError('bank_account_mismatch', str(bank_id))
    if not bank['is_active']:
        raise PostingError('bank_account_inactive', bank['name'])
    if (bank['currency'] or 'UZS') != (doc.get('currency') or 'UZS'):
        raise PostingError('bank_account_currency',
                           f"{bank['name']} ({bank['currency']})")
    return bank['account_id'], bank['id']


def _fx(doc):
    """Foreign-currency memo fields for a line, or empty for plain UZS."""
    if (doc.get('currency') or 'UZS') == 'UZS':
        return {}
    return {'currency': doc.get('currency'),
            'exchange_rate': doc.get('exchange_rate')}


def _line(account_id, debit=0, credit=0, **kw):
    ln = {'account_id': account_id, 'debit': debit, 'credit': credit}
    ln.update(kw)
    return ln


def assert_not_pooled(purpose, conn):
    """Resolve a purpose, refusing an account that sits in the overhead pool.

    THE RULE this enforces: an account may not carry cost_pool='indirect' if
    the same cost is already modeled by a register that calculate_hourly_rate()
    reads — the equipment register, the staff register, the license register.
    Such a cost would then be charged twice: once from its register, once from
    the ledger pool, with no credit to offset it.

    The accounts are seeded 'excluded' and init_db() re-corrects them, but the
    flag is editable on /accounts. This is the guard that stops a well-meaning
    edit there from quietly inflating every rate in the firm. Callers:
    depreciation (9420.1), payroll's admin leg (9420.2), licenses (9420.3).
    """
    account_id = account_id_for(purpose, conn)
    row = conn.execute("SELECT code, cost_pool FROM accounts WHERE id=?",
                       (account_id,)).fetchone()
    if row and row['cost_pool'] == 'indirect':
        raise PostingError('account_in_pool', row['code'])
    return account_id


# ========== 5.1 Sales invoice — счёт-фактура выданный ==========

def _sales_invoice(conn, doc):
    """Dr 4010 total | Cr 9030 net (per line, per project) + Cr 6410.1 VAT."""
    if not doc.get('counterparty_id'):
        raise PostingError('doc_no_counterparty', doc.get('number', ''))
    if not doc['lines']:
        raise PostingError('doc_no_lines', doc.get('number', ''))

    ar = account_id_for('ar', conn)
    revenue = account_id_for('revenue', conn)
    vat_out = account_id_for('vat_output', conn)
    fx = _fx(doc)

    lines = [_line(ar, debit=round(_num(doc['total']), 2),
                   counterparty_id=doc['counterparty_id'],
                   project_id=doc.get('project_id'),
                   description=doc.get('description') or doc['number'],
                   amount_cur=doc.get('total_cur'), **fx)]

    vat_total = 0.0
    for ln in doc['lines']:
        net = round(_num(ln['amount']), 2)
        if net:
            lines.append(_line(
                ln.get('account_id') or revenue, credit=net,
                counterparty_id=doc['counterparty_id'],
                project_id=ln.get('project_id') or doc.get('project_id'),
                phase_id=ln.get('phase_id') or doc.get('phase_id'),
                description=ln.get('description'), **fx))
        vat_total += _num(ln['vat_amount'])

    if round(vat_total, 2):
        lines.append(_line(vat_out, credit=round(vat_total, 2),
                           counterparty_id=doc['counterparty_id'],
                           description='QQS / NDS'))
    return lines, f"Hisob-faktura {doc['number']}"


# ========== 5.2 Purchase invoice — счёт-фактура полученный ==========

def _purchase_invoice(conn, doc):
    """Dr expense/asset per line + Dr 4410 VAT | Cr 6010 total.

    A line tagged with a project defaults to 2010 (основное производство) so it
    lands in that project's cost; an untagged line defaults to 9420
    (административные расходы), which is also the indirect pool the rate engine
    reads.
    """
    if not doc.get('counterparty_id'):
        raise PostingError('doc_no_counterparty', doc.get('number', ''))
    if not doc['lines']:
        raise PostingError('doc_no_lines', doc.get('number', ''))

    ap = account_id_for('ap', conn)
    production = account_id_for('production_cost', conn)
    admin = account_id_for('admin_expense', conn)
    vat_in = account_id_for('vat_input', conn)
    fx = _fx(doc)

    lines = []
    vat_total = 0.0
    for ln in doc['lines']:
        net = round(_num(ln['amount']), 2)
        project_id = ln.get('project_id') or doc.get('project_id')
        if net:
            account = ln.get('account_id') or (production if project_id else admin)
            lines.append(_line(
                account, debit=net,
                counterparty_id=doc['counterparty_id'],
                project_id=project_id,
                phase_id=ln.get('phase_id') or doc.get('phase_id'),
                description=ln.get('description'), **fx))
        vat_total += _num(ln['vat_amount'])

    if round(vat_total, 2):
        lines.append(_line(vat_in, debit=round(vat_total, 2),
                           counterparty_id=doc['counterparty_id'],
                           description='QQS / NDS'))

    lines.append(_line(ap, credit=round(_num(doc['total']), 2),
                       counterparty_id=doc['counterparty_id'],
                       description=doc.get('description') or doc['number'],
                       amount_cur=doc.get('total_cur'), **fx))
    return lines, f"Xarid hisob-fakturasi {doc['number']}"


# ========== 5.3 / 5.4 Cash documents ==========

def _cash_counter_lines(conn, doc, is_inflow):
    """The non-cash side of a cash document, in priority order.

    1. allocations  — settle specific invoices (Cr 4010 / Dr 6010)
    2. lines        — an explicit account: loan principal, interest, tax
                      remittance, payroll payout, dividend, direct expense
    3. remainder    — money with no invoice behind it is an advance
                      (Cr 6310 received / Dr 4310 issued)

    Anything left over with no counterparty at all falls to other operating
    income (9390) or expense (9430) rather than silently unbalancing the entry.
    Those are two different accounts on purpose: 9430 is in the overhead pool,
    which nets credits against debits, so an unattributed *receipt* booked
    there would subtract itself from overhead and lower every man-hour rate.
    """
    fx = _fx(doc)
    total = round(_num(doc['total']), 2)
    counter = []
    covered = 0.0

    settle_account = account_id_for('ar' if is_inflow else 'ap', conn)
    for al in doc['allocations']:
        amount = round(_num(al['amount']), 2)
        if amount <= 0:
            continue
        covered += amount
        counter.append(_line(
            settle_account,
            credit=amount if is_inflow else 0,
            debit=0 if is_inflow else amount,
            counterparty_id=doc.get('counterparty_id'),
            description=str(al['invoice_number'])))

    for ln in doc['lines']:
        amount = round(_num(ln['amount']) or _num(ln['gross']), 2)
        if amount <= 0 or not ln.get('account_id'):
            continue
        covered += amount
        counter.append(_line(
            ln['account_id'],
            credit=amount if is_inflow else 0,
            debit=0 if is_inflow else amount,
            counterparty_id=ln.get('counterparty_id') or doc.get('counterparty_id'),
            project_id=ln.get('project_id') or doc.get('project_id'),
            phase_id=ln.get('phase_id') or doc.get('phase_id'),
            staff_id=ln.get('staff_id'),
            description=ln.get('description'), **fx))

    remainder = round(total - covered, 2)
    if abs(remainder) > 0.005:
        if doc.get('counterparty_id'):
            purpose = 'advances_received' if is_inflow else 'advances_issued'
        else:
            purpose = 'other_income' if is_inflow else 'other_opex'
        counter.append(_line(
            account_id_for(purpose, conn),
            credit=remainder if is_inflow else 0,
            debit=0 if is_inflow else remainder,
            counterparty_id=doc.get('counterparty_id'),
            project_id=doc.get('project_id'),
            description=doc.get('cash_purpose') or doc.get('description'), **fx))
    return counter


def _cash_in(conn, doc):
    """Dr money account | Cr receivables / income / advance."""
    total = round(_num(doc['total']), 2)
    if total <= 0:
        raise PostingError('doc_amount_required', doc.get('number', ''))
    cash, bank_id = cash_account_for(conn, doc)
    lines = [_line(cash, debit=total,
                   counterparty_id=doc.get('counterparty_id'),
                   project_id=doc.get('project_id'),
                   bank_account_id=bank_id,
                   description=doc.get('description') or doc['number'],
                   amount_cur=doc.get('total_cur'), **_fx(doc))]
    lines += _cash_counter_lines(conn, doc, is_inflow=True)
    return lines, f"Pul tushumi {doc['number']}"


def _cash_out(conn, doc):
    """Dr payables / expense / advance | Cr money account."""
    total = round(_num(doc['total']), 2)
    if total <= 0:
        raise PostingError('doc_amount_required', doc.get('number', ''))
    cash, bank_id = cash_account_for(conn, doc)
    lines = _cash_counter_lines(conn, doc, is_inflow=False)
    lines.append(_line(cash, credit=total,
                       counterparty_id=doc.get('counterparty_id'),
                       project_id=doc.get('project_id'),
                       bank_account_id=bank_id,
                       description=doc.get('description') or doc['number'],
                       amount_cur=doc.get('total_cur'), **_fx(doc)))
    return lines, f"Pul chiqimi {doc['number']}"


# ========== 5.5 Payroll — зарплатная ведомость ==========

def _payroll(conn, doc):
    """Accrue one period's payroll from its per-employee lines.

        Dr 2010 (production) / 9420.2 (admin)  gross + social  <- employer cost
        Cr 6710                                gross           <- owed to staff
        Dr 6710 / Cr 6420.1                    PIT             <- withheld
        Cr 6520                                social          <- employer tax

    6710 therefore ends up holding exactly the net pay, which the remittance
    cash_out then clears.

    The admin leg goes to 9420.2, NOT to 9420. Administrative salary is already
    charged to every production rate as `admin_share`, computed from the staff
    register by staff.admin_total_cost(). 9420 is the overhead pool, so posting
    it there would charge the same salary a second time. Measured by replaying
    the real v4 register (27 production, 11 admin) into v5 and flipping the
    flag: +183M UZS/month on the pool, +13% on every cost rate and therefore
    on every quoted price. See assert_not_pooled().
    """
    if not doc['lines']:
        raise PostingError('doc_no_lines', doc.get('number', ''))

    production = account_id_for('production_cost', conn)
    admin = assert_not_pooled('admin_salary_expense', conn)
    payable = account_id_for('payroll_payable', conn)
    pit_acc = account_id_for('pit_payable', conn)
    social_acc = account_id_for('social_payable', conn)

    staff_types = {r['id']: r['staff_type'] for r in conn.execute(
        "SELECT id, staff_type FROM staff")}

    lines = []
    total_pit = total_social = 0.0
    for ln in doc['lines']:
        gross = round(_num(ln['gross']), 2)
        pit = round(_num(ln['pit']), 2)
        social = round(_num(ln['social']), 2)
        staff_id = ln.get('staff_id')
        if gross <= 0 and social <= 0:
            continue
        is_production = staff_types.get(staff_id) == 'production'
        cost_account = ln.get('account_id') or (production if is_production else admin)
        lines.append(_line(cost_account, debit=round(gross + social, 2),
                           staff_id=staff_id,
                           project_id=ln.get('project_id'),
                           description=ln.get('description') or 'Ish haqi'))
        lines.append(_line(payable, credit=gross, staff_id=staff_id))
        total_pit += pit
        total_social += social

    if round(total_pit, 2):
        lines.append(_line(payable, debit=round(total_pit, 2),
                           description='JSHDS ushlab qolindi'))
        lines.append(_line(pit_acc, credit=round(total_pit, 2), description='JSHDS'))
    if round(total_social, 2):
        lines.append(_line(social_acc, credit=round(total_social, 2),
                           description='Ijtimoiy soliq'))
    if not lines:
        raise PostingError('doc_no_lines', doc.get('number', ''))
    return lines, f"Ish haqi {doc.get('period') or doc['date'][:7]}"


# ========== 5.7 Dividend ==========

def _dividend(conn, doc):
    """Declaration: Dr 8710 retained earnings | Cr 6610 payable to the founder.

    The payout itself is a cash_out document with a line on 6610.
    """
    total = round(_num(doc['total']), 2)
    if total <= 0:
        raise PostingError('doc_amount_required', doc.get('number', ''))
    return ([_line(account_id_for('retained_earnings', conn), debit=total,
                   description=doc.get('description') or 'Dividend'),
             _line(account_id_for('dividends_payable', conn), credit=total,
                   counterparty_id=doc.get('counterparty_id'))],
            f"Dividend {doc['number']}")


# ========== Loan issue ==========

def _loan(conn, doc):
    """Money moving on a loan.

    olgan  (we borrowed) — Dr money | Cr 6820/7820
    bergan (we lent)     — Dr 5820  | Cr money
    """
    if not doc.get('loan_id'):
        raise PostingError('doc_loan_required', doc.get('number', ''))
    loan = conn.execute("SELECT * FROM loans WHERE id=?", (doc['loan_id'],)).fetchone()
    if not loan:
        raise PostingError('doc_loan_required', str(doc['loan_id']))

    total = round(_num(doc['total']), 2)
    cash, bank_id = cash_account_for(conn, doc)
    fx = _fx(doc)
    fx['bank_account_id'] = bank_id
    if loan['loan_type'] == 'olgan':
        liability = account_id_for(
            'loan_long_in' if loan['term'] == 'long' else 'loan_short_in', conn)
        lines = [_line(cash, debit=total, counterparty_id=loan['counterparty_id'], **fx),
                 _line(liability, credit=total, counterparty_id=loan['counterparty_id'],
                       description=doc.get('description') or 'Qarz olindi')]
    else:
        asset = account_id_for('loan_issued', conn)
        lines = [_line(asset, debit=total, counterparty_id=loan['counterparty_id'],
                       description=doc.get('description') or 'Qarz berildi'),
                 _line(cash, credit=total, counterparty_id=loan['counterparty_id'], **fx)]
    return lines, f"Qarz {doc['number']}"


# ========== 5.8 Opening balances ==========

def _opening(conn, doc):
    """Every opening balance against 0000 (вспомогательный счёт).

    When the full set has been entered and the books actually balance, 0000
    nets to zero — that is the check, and it is why a single suspense account
    is the standard way to key in opening balances.
    """
    if not doc['lines']:
        raise PostingError('doc_no_lines', doc.get('number', ''))
    offset = account_id_for('opening_offset', conn)

    lines = []
    net = 0.0
    for ln in doc['lines']:
        debit = round(_num(ln['debit']), 2)
        credit = round(_num(ln['credit']), 2)
        if not (debit or credit) or not ln.get('account_id'):
            continue
        lines.append(_line(ln['account_id'], debit=debit, credit=credit,
                           counterparty_id=ln.get('counterparty_id'),
                           project_id=ln.get('project_id'),
                           staff_id=ln.get('staff_id'),
                           description=ln.get('description') or 'Boshlangich qoldiq'))
        net += debit - credit

    if not lines:
        raise PostingError('doc_no_lines', doc.get('number', ''))
    if abs(round(net, 2)) > 0.005:
        lines.append(_line(offset,
                           debit=round(-net, 2) if net < 0 else 0,
                           credit=round(net, 2) if net > 0 else 0,
                           description='Boshlangich qoldiqlar (0000)'))
    return lines, f"Boshlangich qoldiqlar {doc['number']}"


# ========== 5.10 Manual entry ==========

def _manual(conn, doc):
    """User-entered debits and credits, validated by post_entry()."""
    if not doc['lines']:
        raise PostingError('doc_no_lines', doc.get('number', ''))
    lines = []
    for ln in doc['lines']:
        debit = round(_num(ln['debit']), 2)
        credit = round(_num(ln['credit']), 2)
        if not (debit or credit) or not ln.get('account_id'):
            continue
        lines.append(_line(ln['account_id'], debit=debit, credit=credit,
                           counterparty_id=ln.get('counterparty_id'),
                           project_id=ln.get('project_id'),
                           phase_id=ln.get('phase_id'),
                           staff_id=ln.get('staff_id'),
                           description=ln.get('description')))
    if not lines:
        raise PostingError('doc_no_lines', doc.get('number', ''))
    return lines, doc.get('description') or f"Qolda provodka {doc['number']}"


RULES = {
    'sales_invoice': _sales_invoice,
    'purchase_invoice': _purchase_invoice,
    'cash_in': _cash_in,
    'cash_out': _cash_out,
    'payroll': _payroll,
    'dividend': _dividend,
    'loan': _loan,
    'opening': _opening,
    'manual': _manual,
}


def build_entry_lines(conn, doc):
    """(lines, memo) for one document. Raises PostingError for an unknown type."""
    rule = RULES.get(doc['doc_type'])
    if not rule:
        raise PostingError('doc_type_unknown', doc['doc_type'])
    return rule(conn, doc)
