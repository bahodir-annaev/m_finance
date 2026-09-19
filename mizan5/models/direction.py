# -*- coding: utf-8 -*-
"""Internal / external / financing — a derived reporting axis over the ledger.

v4 stored `direction` on every `transactions` row: external was client- and
project-facing money (income invoices, outsourcing, materials), internal was
the firm's own running costs (salary, rent, utilities, tax, licences). v5 has
no such column, deliberately — the overhead question moved to
`accounts.cost_pool` and the "does this have a supplier" question to the
counterparty. But the monthly reading v4 users depend on went with it: how
much of this month's outflow went to client projects, and how much kept the
office open.

This module recovers that reading by classifying each *document* from the
posting it produced. Nothing here writes, and nothing here is an input to
`posting.py` or to the rate engine. **Direction is a lens, never a fact on a
row** — putting it into a posting rule, or letting it decide a `cost_pool`,
would break the double-count rule.

The ladder, in order — the order is the whole design:

    1. payroll document                            -> internal
    2. loan/dividend document, or a line on a
       financing account                           -> financing
    3. the document settles invoices               -> whatever those are
    4. a sales invoice, or a line on a client
       account (4010 / 9030 / 6310)                -> external
    5. a line carrying a project                   -> external
    6. an invoice with a line on a direct-cost
       account (2010 / 9130)                       -> external
    7. otherwise                                   -> internal

Rung 6 is why the order matters. 2010 is debited by two unrelated things:
outsourcing and materials on a client project, *and* production payroll — and
`import_v4.TX_ACCOUNT_MAP` puts v4's `maosh`/`premiya` there too. 2010 alone
therefore cannot decide. Rung 1 removes payroll documents first, and rung 6
then admits 2010 only on an *invoice* — which is exactly the shape the
migration gives a v4 external cost, while a v4 internal cost arrives as a bare
cash_out and falls through to rung 7. That is what makes the derived value
reproduce v4's stored one.

Money with no counterparty behind it lands on 9390 / 9430 and stays internal.
It must: 9390 vs 9430 is already load-bearing for the overhead pool, and an
unattributed receipt is not client money just because someone paid it in.

An entry with no document (depreciation, FX revaluation, the period close —
all of which leave `journal_entries.document_id` NULL by design) counts as
internal. None of them touches a money account, so the cash-flow split is
unaffected either way.
"""
from .base import get_db
from .ledger import PostingError, account_id_for

DIRECTIONS = ('external', 'internal', 'financing')
EXTERNAL, INTERNAL, FINANCING = DIRECTIONS

# Accounts are named by purpose, never by code, so a firm on a different chart
# only has to re-point account_map — the same rule posting.py follows.
CLIENT_PURPOSES = ('ar', 'revenue', 'advances_received')
DIRECT_COST_PURPOSES = ('production_cost', 'cogs')
FINANCING_PURPOSES = ('loan_short_in', 'loan_long_in', 'loan_issued',
                      'dividends_payable', 'retained_earnings',
                      'interest_expense', 'interest_income')

INVOICE_DOC_TYPES = ('sales_invoice', 'purchase_invoice')
FINANCING_DOC_TYPES = ('loan', 'dividend')


def _purpose_account_ids(purposes, conn):
    """Resolve a purpose tuple to account ids, skipping any the chart lacks.

    A missing purpose loses that one signal rather than raising: direction is a
    report, and a report must not be the thing that takes the app down.
    """
    out = set()
    for purpose in purposes:
        try:
            out.add(account_id_for(purpose, conn))
        except PostingError:
            continue
    return out


def _blank(doc_type):
    return {'doc_type': doc_type, 'project': False, 'client': False,
            'direct': False, 'financing': False}


def _collect_flags(conn, client_ids, direct_ids, financing_ids):
    """Per document: which account families and analytics its posting touches.

    Posted and void documents are read from the posting itself. Drafts have no
    posting yet, so they are read from the lines they would post from — a
    filter that only worked after posting would hide exactly the rows someone
    is still working on.
    """
    flags = {}

    def mark(doc_id, doc_type, account_id, project_id):
        f = flags.get(doc_id)
        if f is None:
            f = flags[doc_id] = _blank(doc_type)
        if project_id:
            f['project'] = True
        if account_id in client_ids:
            f['client'] = True
        if account_id in direct_ids:
            f['direct'] = True
        if account_id in financing_ids:
            f['financing'] = True

    for r in conn.execute(
            "SELECT d.id AS doc_id, d.doc_type AS doc_type,"
            " jl.account_id AS account_id, jl.project_id AS project_id"
            " FROM documents d"
            " JOIN journal_entries je ON je.document_id = d.id"
            " JOIN journal_lines jl ON jl.entry_id = je.id"):
        mark(r['doc_id'], r['doc_type'], r['account_id'], r['project_id'])

    for r in conn.execute(
            "SELECT d.id AS doc_id, d.doc_type AS doc_type,"
            " dl.account_id AS account_id,"
            " COALESCE(dl.project_id, d.project_id) AS project_id"
            " FROM documents d LEFT JOIN document_lines dl ON dl.document_id = d.id"
            " WHERE d.entry_id IS NULL"):
        mark(r['doc_id'], r['doc_type'], r['account_id'], r['project_id'])

    return flags


def _resolve(doc_id, flags, allocations, cache, seen=()):
    """Apply the ladder to one document. See the module docstring."""
    if doc_id in cache:
        return cache[doc_id]
    f = flags.get(doc_id)
    if f is None:
        return INTERNAL

    doc_type = f['doc_type']
    settles = allocations.get(doc_id)

    if doc_type == 'payroll':
        result = INTERNAL
    elif doc_type in FINANCING_DOC_TYPES or f['financing']:
        result = FINANCING
    elif settles and doc_id not in seen:
        # A payment is whatever it settles. Invoices never appear as a
        # payment_doc_id, so this recursion is one level deep; `seen` only
        # guards against hand-edited data that made a cycle.
        kinds = {_resolve(inv, flags, allocations, cache, seen + (doc_id,))
                 for inv in settles}
        result = kinds.pop() if len(kinds) == 1 else (
            EXTERNAL if EXTERNAL in kinds else INTERNAL)
    elif doc_type == 'sales_invoice' or f['client']:
        result = EXTERNAL
    elif f['project']:
        result = EXTERNAL
    elif doc_type in INVOICE_DOC_TYPES and f['direct']:
        result = EXTERNAL
    else:
        result = INTERNAL

    cache[doc_id] = result
    return result


def document_directions(conn=None, doc_ids=None):
    """{document_id: direction} for every document, or just `doc_ids`.

    Classification always runs over the whole register even when a subset is
    asked for: rung 3 has to see the invoices a payment settles, and those are
    routinely outside the page being rendered.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        client_ids = _purpose_account_ids(CLIENT_PURPOSES, conn)
        direct_ids = _purpose_account_ids(DIRECT_COST_PURPOSES, conn)
        financing_ids = _purpose_account_ids(FINANCING_PURPOSES, conn)
        flags = _collect_flags(conn, client_ids, direct_ids, financing_ids)

        allocations = {}
        for r in conn.execute(
                "SELECT payment_doc_id, invoice_doc_id FROM payment_allocations"):
            allocations.setdefault(r['payment_doc_id'], []).append(r['invoice_doc_id'])
    finally:
        if own:
            conn.close()

    cache = {}
    for doc_id in flags:
        _resolve(doc_id, flags, allocations, cache)
    if doc_ids is None:
        return cache
    wanted = set(doc_ids)
    return {k: v for k, v in cache.items() if k in wanted}


def document_direction(doc_id, conn=None):
    """The direction of one document."""
    return document_directions(conn).get(doc_id, INTERNAL)


def cash_turnover_by_direction(account_ids, date_from=None, date_to=None):
    """{period: {direction: {'debit', 'credit'}}} across the given accounts.

    The same rows `ledger.monthly_turnover()` sums, split by the direction of
    the document behind each entry. Every row belongs to exactly one document
    and every document to exactly one direction, so the split always adds back
    up to the undivided turnover — a cash movement can be neither lost nor
    counted twice.
    """
    if not account_ids:
        return {}
    directions = document_directions()
    conn = get_db()
    try:
        ph = ','.join('?' * len(account_ids))
        sql = (f"SELECT je.period AS period, je.document_id AS doc_id,"
               f" COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
               f" FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               f" WHERE jl.account_id IN ({ph})")
        params = list(account_ids)
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        sql += " GROUP BY je.period, je.document_id"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    out = {}
    for r in rows:
        kind = directions.get(r['doc_id'], INTERNAL)
        bucket = out.get(r['period'])
        if bucket is None:
            bucket = out[r['period']] = {
                d: {'debit': 0.0, 'credit': 0.0} for d in DIRECTIONS}
        bucket[kind]['debit'] += r['d']
        bucket[kind]['credit'] += r['c']
    return out
