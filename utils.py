"""Shared helpers — formatting, translation, rendering."""
from flask import request, render_template, get_flashed_messages
from markupsafe import Markup
from translations import get_text

SUPPORTED_LANGS = ['uz', 'en', 'ru']


def get_lang():
    lang = request.cookies.get('mizan_lang', 'uz')
    return lang if lang in SUPPORTED_LANGS else 'uz'


def t(key, *args):
    return get_text(get_lang(), key, *args)


def fmt(n, decimals=0):
    if n is None:
        return '0'
    if decimals == 0:
        return f'{n:,.0f}'
    return f'{n:,.{decimals}f}'


def render_page(page, template_name, **ctx):
    """Render a page template with standard context (page, t, fmt, lang).

    If the caller doesn't pass msg explicitly, fall back to a flash()-queued
    message from a just-completed POST/redirect.
    """
    if 'msg' not in ctx:
        flashed = get_flashed_messages()
        ctx['msg'] = Markup(''.join(flashed)) if flashed else ''
    return render_template(template_name, page=page, t=t, fmt=fmt, lang=get_lang(), **ctx)
