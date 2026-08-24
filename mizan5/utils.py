"""Shared helpers — formatting, translation, rendering."""
from flask import render_template, request, get_flashed_messages
from markupsafe import Markup

from translations import get_text

SUPPORTED_LANGS = ['uz', 'en', 'ru']


def get_lang():
    lang = request.cookies.get('mizan_lang', 'uz')
    return lang if lang in SUPPORTED_LANGS else 'uz'


def t(key, *args):
    return get_text(get_lang(), key, *args)


def fmt(n, decimals=0):
    """Thousands-separated number for display. None reads as zero."""
    if n is None:
        return '0'
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if decimals == 0:
        return f'{n:,.0f}'
    return f'{n:,.{decimals}f}'


def fmt_signed(n, decimals=0):
    """Like fmt() but always carries an explicit sign — for variance columns."""
    if n is None:
        return '0'
    return ('+' if n > 0 else '') + fmt(n, decimals)


def account_label(row, lang=None):
    """'5110 — Расчетные счета', in the active language where a name exists."""
    if not row:
        return ''
    lang = lang or get_lang()
    name = row.get(f'name_{lang}') or row.get('name_ru') or row.get('name_uz') or ''
    return f"{row.get('code', '')} — {name}"


def render_page(page, template_name, **ctx):
    """Render with the standard context injected (page, t, fmt, lang).

    A msg not passed explicitly falls back to a flash() queued by a
    just-completed POST/redirect.
    """
    if 'msg' not in ctx:
        flashed = get_flashed_messages()
        ctx['msg'] = Markup(''.join(flashed)) if flashed else ''
    return render_template(template_name, page=page, t=t, fmt=fmt,
                           fmt_signed=fmt_signed, account_label=account_label,
                           lang=get_lang(), **ctx)


def parse_float(value, default=0.0):
    """Form floats: accepts '1 234,56' and '1,234.56', empty → default."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(' ', '').replace(' ', '')
    if not s:
        return default
    # A comma is a decimal separator only when no dot is present.
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return default


def parse_int(value, default=None):
    try:
        s = str(value).strip()
        return int(s) if s else default
    except (TypeError, ValueError):
        return default


def form_rows(form, prefix, fields):
    """Collect repeated modal grid inputs named '<prefix>_<field>' into dicts.

    The line-item grids post parallel arrays (line_amount, line_vat_rate, …);
    this zips them back into one dict per row and drops fully blank rows.
    """
    lists = {f: form.getlist(f'{prefix}_{f}') for f in fields}
    count = max((len(v) for v in lists.values()), default=0)
    rows = []
    for i in range(count):
        row = {f: (lists[f][i] if i < len(lists[f]) else '') for f in fields}
        if any(str(v).strip() for v in row.values()):
            rows.append(row)
    return rows
