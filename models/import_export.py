"""Excel import utilities."""
import os
from datetime import datetime
from .base import get_db, write_audit_log, resolve_entity_id, record_unresolved_import, get_rate_for_date

_COL_PATTERNS = {
    'date': ['sana', 'date', 'дата', 'sanasi'],
    'project': ['proekt', 'proeyekt', 'project', 'проект', 'loyiha'],
    'income_desc': ['tushum nomi', 'tushum', 'income', 'доход'],
    'expense_desc': ['harajat nomi', 'harajat', 'expense', 'расход'],
    'client': ['buyurtmachi', 'mijoz', 'client', 'клиент', 'заказчик'],
    'responsible': ["mas'ul", 'responsible', 'ответственный', 'javobgar'],
    'paid_to': ['kimga', 'paid to', 'кому', "to'langan"],
    'doc_id': ['hujjat', 'document', 'документ', 'doc'],
    'contract_uzs': ['shartnoma', 'contract', 'контракт'],
    'amount': ['summa', 'amount', 'сумма', 'sum'],
    'paid': ["to'langan", 'paid', 'оплачено', 'tulangan'],
    'remainder': ['qoldiq', 'remain', 'остаток', 'balance'],
    'payment_type': ["to'lov turi", 'payment type', 'тип оплаты'],
    'category': ['kategoriya', 'category', 'категория', 'turi'],
    'status': ['status', 'holat', 'статус'],
    'notes': ['sharx', 'izoh', 'notes', 'comment', 'примечание'],
    'deadline': ['muddat', 'deadline', 'срок'],
    'currency': ['valyuta', 'currency', 'валюта'],
}

# Mixed income/expense-per-row sheets (e.g. "Pul beruvchi" / "Приход" / "расходы" /
# "прочие расходы" / "Вид расхода") use different header vocabulary than the
# external/internal sheets above, so they get their own pattern set.
_COL_PATTERNS_MIX = {
    'date': ['sana', 'date', 'дата', 'sanasi'],
    'project': ['proekt', 'proeyekt', 'project', 'проект', 'loyiha'],
    'counterparty': ['pul beruvchi', "to'lovchi", 'kontragent', 'counterparty'],
    'purpose': ['назначение', 'maqsad', 'purpose'],
    'income_amount': ['приход', 'tushum', 'income'],
    # checked before 'expense_amount' — 'расход' is a substring of all three of these too
    'expense_type': ['вид расхода', 'harajat turi', 'expense type'],
    'notes': ['sharx', 'izoh', 'notes', 'comment', 'примечание', 'статья расходов'],
    # "прочие расходы" / "расходы валюта" always hold USD, whatever the cell's
    # number format shows. Checked before 'expense_amount' — 'расход' is a
    # substring of both, so the plain-UZS pattern would otherwise swallow them.
    'expense_amount_usd': ['прочие расходы', 'расходы валюта'],
    'expense_amount': ['расход', 'harajat', 'expense'],
    'doc_id': ['hujjat', 'document', 'документ', 'doc'],
}

# Fields that hold money — cast to float.
_NUMERIC_FIELDS = ('amount', 'paid', 'contract_uzs', 'contract_usd', 'amount_usd',
                    'contract_amount', 'income_amount', 'expense_amount',
                    'income_amount_usd', 'expense_amount_usd')
# SUM rather than overwrite when more than one column in a row maps to the field.
_ACCUMULATE_FIELDS = ('income_amount', 'income_amount_usd')
# The expense columns ("расходы" in UZS vs "прочие расходы" / "расходы валюта" in
# USD) are alternative statements of one row's expense, not additive components:
# the first non-empty column on the row wins and later ones are ignored. UZS still
# takes precedence over USD row-wide — that happens further down, in the mix branch.
_FIRST_WINS_FIELDS = ('expense_amount', 'expense_amount_usd')

# Sub-header tags that mark a merged "Приход"/"расходы" column's USD counterpart
# (e.g. "сум" / "$" split under one merged label) rather than real transaction data.
_CURRENCY_SUBHEADER_TAGS = ('сум', 'сумма', '$', 'usd', 'доллар')

# "Вид расхода" values that are project-related costs, not firm overhead — these
# must land on the External Aylanma page (direction='external') like outsourcing/
# material always have elsewhere in this app, not the Internal page. 'outsourcing'
# is also the literal tx_type calculate_project_cost() sums per-project, so it's
# translated to that canonical code rather than kept as raw Cyrillic text.
_EXTERNAL_EXPENSE_TYPES = {
    'банк': 'bank',
    'аутсорсинг': 'outsourcing',
    'налог': 'soliq',
}
# Free-text markers in "Примечание" that force the same external classification
# even when "Вид расхода" itself doesn't say so (apostrophe variants included).
_EXTERNAL_NOTE_KEYWORDS = ['ндс', 'фойда', "qo'shimcha", "qo‘shimcha", "qo’shimcha", 'qoshimcha']


def _is_external_expense(expense_type, notes):
    if (expense_type or '').strip().lower() in _EXTERNAL_EXPENSE_TYPES:
        return True
    note_l = (notes or '').lower()
    return any(kw in note_l for kw in _EXTERNAL_NOTE_KEYWORDS)


def _row_snippet(row, max_cells=8, max_len=160):
    """Short readable preview of a raw row, for skip_log entries."""
    vals = [str(v) for v in row[:max_cells] if v is not None]
    return ' | '.join(vals)[:max_len]


def _parse_numeric(val):
    """Parse a cell into (number, is_usd). A leading '$' on a string value marks
    it as a USD amount (e.g. "$1500", "$ 1,500") so it can be routed to a *_usd
    field rather than failing the float cast and silently becoming 0."""
    if val is None:
        return 0, False
    if isinstance(val, (int, float)):
        return float(val), False
    s = str(val).strip()
    if not s:
        return 0, False
    is_usd = s.startswith('$')
    if is_usd:
        s = s[1:].strip()
    s = s.replace(',', '').replace(' ', '')
    try:
        return float(s), is_usd
    except (ValueError, TypeError):
        return 0, False


def _match_column(col_name, patterns_dict):
    if not col_name:
        return None
    name = str(col_name).lower().strip().replace('\n', ' ')
    for field, keywords in patterns_dict.items():
        for kw in keywords:
            if kw in name:
                return field
    return None


def analyze_excel_for_import(filepath):
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    result = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_list = list(ws.iter_rows(max_row=5, values_only=True))
        if not rows_list:
            continue
        header_row = rows_list[0]
        label_row_idx = 0
        if all(v is None or str(v).strip() == '' for v in header_row[:3]):
            if len(rows_list) > 1:
                header_row = rows_list[1]
                label_row_idx = 1

        # Some sheets add a third header row splitting amount columns into
        # "сум" (UZS) / "$" (USD) sub-columns under one merged label. Detect it
        # so data rows don't get misread starting from that sub-header row.
        sub_row = rows_list[label_row_idx + 1] if len(rows_list) > label_row_idx + 1 else None
        sub_row_is_header = False
        if sub_row is not None:
            sub_vals = [str(v).strip().lower() for v in sub_row if v is not None]
            if sub_vals and all(v in _CURRENCY_SUBHEADER_TAGS for v in sub_vals):
                sub_row_is_header = True
        header_idx = label_row_idx + 1 if sub_row_is_header else label_row_idx

        col_names_lower = ' '.join(str(c).lower() for c in header_row if c)
        sheet_type = 'unknown'
        if 'pul beruvchi' in col_names_lower or ('приход' in col_names_lower and 'расход' in col_names_lower):
            sheet_type = 'mix'
        elif 'tushum' in col_names_lower and 'harajat' in col_names_lower and 'proeyekt' in col_names_lower:
            sheet_type = 'external'
        elif 'tushum' in col_names_lower and 'harajat' in col_names_lower:
            sheet_type = 'internal'
        elif 'ish haqi' in col_names_lower or ('harajat' in col_names_lower and 'proekt' not in col_names_lower):
            sheet_type = 'internal'
        elif 'plan' in col_names_lower or 'fakt' in col_names_lower:
            sheet_type = 'budget'
        elif 'soat' in col_names_lower and 'narx' in col_names_lower:
            sheet_type = 'hourly'
        elif 'kpi' in col_names_lower:
            sheet_type = 'kpi'

        patterns = _COL_PATTERNS_MIX if sheet_type == 'mix' else _COL_PATTERNS
        cols = []
        last_amount_field = None
        for i, col_name in enumerate(header_row):
            sub_val = ''
            if sub_row_is_header and sub_row is not None and i < len(sub_row) and sub_row[i] is not None:
                sub_val = str(sub_row[i]).strip().lower()
            if col_name is None:
                # merge continuation under a merged "Приход"/"расходы" label — only
                # meaningful if the sub-header row marks it as the USD counterpart
                if sub_val in ('$', 'usd', 'доллар') and last_amount_field:
                    cols.append({'index': i, 'original': f'{last_amount_field} ($)',
                                 'matched': last_amount_field + '_usd'})
                continue
            matched = _match_column(col_name, patterns)
            last_amount_field = matched if matched in ('income_amount', 'expense_amount') else None
            cols.append({'index': i, 'original': str(col_name).replace('\n', ' ').strip(), 'matched': matched})
        row_count = ws.max_row - header_idx - 1 if ws.max_row else 0
        result.append({'name': sheet_name, 'type': sheet_type, 'columns': cols,
                       'row_count': row_count, 'header_idx': header_idx})
    wb.close()
    return result


def import_excel_data(filepath, sheet_name, target_table, column_mapping, header_idx=0):
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    conn = get_db()
    imported = 0
    skipped = 0
    skip_log = []
    max_skip_log = 200
    file_name = os.path.basename(filepath)

    def _log_skip(row_num, reason, raw):
        conn.execute(
            "INSERT INTO import_skip_log (file_name, sheet_name, row_num, reason, raw) VALUES (?,?,?,?,?)",
            (file_name, sheet_name, row_num, reason, raw)
        )
        if len(skip_log) < max_skip_log:
            skip_log.append({'row': row_num, 'reason': reason, 'raw': raw})

    # Map legacy target_table names to direction in unified transactions table
    if target_table in ('external_transactions', 'external'):
        direction = 'external'
    elif target_table in ('mixed_transactions', 'mix'):
        direction = 'mix'
    else:
        direction = 'internal'

    for row_num, row in enumerate(ws.iter_rows(min_row=header_idx + 2, values_only=True),
                                   start=header_idx + 2):
        if all(v is None for v in row):
            # Fully blank row — almost always trailing sheet padding, not worth logging.
            skipped += 1
            continue

        data = {}
        for col_idx_str, db_field in column_mapping.items():
            col_idx = int(col_idx_str)
            if col_idx < len(row):
                val = row[col_idx]
                if val is not None:
                    if db_field in ('date', 'deadline') and hasattr(val, 'strftime'):
                        val = val.strftime('%Y-%m-%d')
                    elif db_field in _NUMERIC_FIELDS:
                        val, is_usd = _parse_numeric(val)
                        # An inline "$" on an income/expense amount reroutes it to
                        # the matching USD field, same as a dedicated "$" sub-column.
                        if is_usd and db_field in ('income_amount', 'expense_amount'):
                            db_field = db_field + '_usd'
                    else:
                        val = str(val).strip() if val else ''
                    if db_field in _ACCUMULATE_FIELDS and db_field in data:
                        data[db_field] = (data[db_field] or 0) + (val or 0)
                    elif not (db_field in _FIRST_WINS_FIELDS and data.get(db_field)):
                        data[db_field] = val

        if not data or not any(data.values()):
            skipped += 1
            _log_skip(row_num, 'no_data', _row_snippet(row))
            continue

        if direction == 'external':
            proj_name = data.get('project', '')
            project_id = resolve_entity_id(conn, 'project', proj_name) if proj_name else None

            client_name = data.get('client', '')
            counterparty_id = resolve_entity_id(conn, 'counterparty', client_name) if client_name else None

            tx_date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
            income_desc = data.get('income_desc', '')
            expense_desc = data.get('expense_desc', '')
            description = income_desc if income_desc else expense_desc
            tx_type = 'tushum' if income_desc else 'outsourcing'
            amount = data.get('amount', 0) or 0
            paid = data.get('paid', 0) or 0
            status = "To'langan" if paid >= amount and amount > 0 else 'Kutilmoqda'

            cur = conn.execute('''INSERT INTO transactions
                (direction, tx_type, date, ref_id, project_id, counterparty_id, client, description,
                 contract_amount, amount, paid, currency, payment_type, deadline, notes, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('external', tx_type, tx_date, data.get('doc_id', ''), project_id, counterparty_id,
                 client_name, description,
                 data.get('contract_uzs', 0), amount, paid,
                 data.get('currency', 'UZS'), data.get('payment_type', 'bank'),
                 data.get('deadline', ''), data.get('notes', ''), status))
            tx_id = cur.lastrowid

            if proj_name and not project_id:
                record_unresolved_import(conn, 'project', proj_name, tx_id)
            if client_name and not counterparty_id:
                record_unresolved_import(conn, 'counterparty', client_name, tx_id, field='client')

            imported += 1

        elif direction == 'mix':
            tx_date = data.get('date', datetime.now().strftime('%Y-%m-%d'))

            payer_name = data.get('counterparty', '')
            counterparty_id = resolve_entity_id(conn, 'counterparty', payer_name) if payer_name else None

            proj_name = data.get('project', '')
            project_id = resolve_entity_id(conn, 'project', proj_name) if proj_name else None

            income_uzs = data.get('income_amount', 0) or 0
            income_usd = data.get('income_amount_usd', 0) or 0
            expense_uzs = data.get('expense_amount', 0) or 0
            expense_usd = data.get('expense_amount_usd', 0) or 0
            expense_type = (data.get('expense_type', '') or '').strip()
            purpose = (data.get('purpose', '') or '').strip()
            notes_val = data.get('notes', '') or ''

            currency, exchange_rate, amount_usd = 'UZS', None, None

            if income_uzs or income_usd:
                sub_direction, tx_type = 'external', 'tushum'
                if income_uzs:
                    amount = paid = income_uzs
                else:
                    # USD value goes to amount_usd; paid/amount hold the converted UZS
                    currency = 'USD'
                    exchange_rate = get_rate_for_date(tx_date) or 0
                    amount_usd = income_usd
                    amount = paid = income_usd * exchange_rate
                description = purpose
                client, paid_to, field = payer_name, '', 'client'
            elif expense_uzs or expense_usd:
                if _is_external_expense(expense_type, notes_val):
                    sub_direction = 'external'
                    tx_type = _EXTERNAL_EXPENSE_TYPES.get(expense_type.lower(), expense_type or 'outsourcing')
                else:
                    sub_direction = 'internal'
                    tx_type = expense_type or 'overhead'
                if expense_uzs:
                    amount = paid = expense_uzs
                else:
                    # USD value goes to amount_usd; paid/amount hold the converted UZS
                    currency = 'USD'
                    exchange_rate = get_rate_for_date(tx_date) or 0
                    amount_usd = expense_usd
                    amount = paid = expense_usd * exchange_rate
                description = purpose or expense_type
                client, paid_to, field = '', payer_name, 'paid_to'
            else:
                skipped += 1
                _log_skip(row_num, 'no_amount', _row_snippet(row))
                continue

            cur = conn.execute('''INSERT INTO transactions
                (direction, tx_type, date, ref_id, project_id, counterparty_id, client, paid_to,
                 description, amount, paid, currency, exchange_rate, amount_usd, payment_type,
                 notes, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (sub_direction, tx_type, tx_date, data.get('doc_id', ''), project_id, counterparty_id,
                 client, paid_to, description, amount, paid, currency, exchange_rate, amount_usd, 'bank',
                 data.get('notes', ''), "To'langan"))
            tx_id = cur.lastrowid

            if proj_name and not project_id:
                record_unresolved_import(conn, 'project', proj_name, tx_id)
            if payer_name and not counterparty_id:
                record_unresolved_import(conn, 'counterparty', payer_name, tx_id, field=field)

            imported += 1

        else:  # internal
            tx_date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
            paid_to = data.get('paid_to', '')
            counterparty_id = resolve_entity_id(conn, 'counterparty', paid_to) if paid_to else None

            desc = (data.get('income_desc', '') or data.get('expense_desc', '') or
                    data.get('notes', '') or 'Import')
            if paid_to:
                desc = f"{desc} → {paid_to}"
            amount = data.get('amount', 0) or 0
            paid = data.get('paid', 0) or 0
            tx_type = data.get('category', 'overhead')

            cur = conn.execute('''INSERT INTO transactions
                (direction, tx_type, date, ref_id, counterparty_id, paid_to, description,
                 contract_amount, amount, paid, currency, payment_type, notes, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('internal', tx_type, tx_date, data.get('doc_id', ''), counterparty_id, paid_to, desc,
                 data.get('contract_uzs', 0), amount, paid, 'UZS', 'bank',
                 data.get('notes', ''), 'paid'))
            tx_id = cur.lastrowid

            if paid_to and not counterparty_id:
                record_unresolved_import(conn, 'counterparty', paid_to, tx_id, field='paid_to')

            imported += 1

    conn.commit()
    conn.close()
    wb.close()

    write_audit_log('import', 'transactions', context=f'Excel import: {imported} rows from {sheet_name}')

    conn2 = get_db()
    unresolved_projects = conn2.execute(
        "SELECT COUNT(DISTINCT raw_name) FROM unresolved_imports WHERE entity_type='project' AND resolved=0"
    ).fetchone()[0]
    unresolved_counterparties = conn2.execute(
        "SELECT COUNT(DISTINCT raw_name) FROM unresolved_imports WHERE entity_type='counterparty' AND resolved=0"
    ).fetchone()[0]
    conn2.close()

    return {
        'imported': imported, 'skipped': skipped, 'skip_log': skip_log,
        'unresolved_projects': unresolved_projects,
        'unresolved_counterparties': unresolved_counterparties,
    }
