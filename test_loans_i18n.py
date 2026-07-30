"""Regression test: /loans sahifasi i18n keys mavjud va to'liq.

Run: python test_loans_i18n.py
Bug: /loans sahifasi to'liq Uzbek hardcoded edi, EN/RU foydalanuvchilarga noto'g'ri til.
Fix by /qa: 22 ta loans_* kalit qo'shildi UZ/EN/RU, app.py:1995-2032 t() bilan o'rab chiqildi.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from translations import TRANSLATIONS, get_text

REQUIRED_KEYS = [
    'loans_title', 'loans_subtitle',
    'loans_taken_label', 'loans_given_label',
    'loans_total_prefix', 'loans_net_label', 'loans_net_sub',
    'loans_overdue_label', 'loans_overdue_sub',
    'loans_taken_section', 'loans_given_section',
    'loans_taken_th_giver', 'loans_given_th_taker',
    'loans_th_currency', 'loans_th_taken_date', 'loans_th_given_date',
    'loans_th_due', 'loans_th_progress', 'loans_th_returned',
    'loans_empty', 'loans_msg_added', 'loans_msg_payment',
]

LANGS = ['uz', 'en', 'ru']


def test_all_keys_exist_in_all_languages():
    """Har bir til 22 ta loans_* kalitga ega bo'lishi kerak."""
    for lang in LANGS:
        for key in REQUIRED_KEYS:
            assert key in TRANSLATIONS[lang], f"{lang} tilida '{key}' kaliti yo'q"
    print(f"  [OK] test_all_keys_exist_in_all_languages ({len(REQUIRED_KEYS)} kalit x 3 til)")


def test_no_uzbek_text_leaks_in_en():
    """EN tilida barcha loans_* qiymatlari Uzbek belgilarsiz bo'lishi kerak (apostrof yo' o' g')."""
    UZBEK_MARKERS = ["o'g", "g'", "o'", "Qarz", "qarz", "Olgan", "Bergan", "Muddat", "Holat"]
    for key in REQUIRED_KEYS:
        en_val = TRANSLATIONS['en'][key]
        for marker in UZBEK_MARKERS:
            assert marker not in en_val, f"EN '{key}' ichida Uzbek matn topildi: '{marker}' in '{en_val}'"
    print("  [OK] test_no_uzbek_text_leaks_in_en")


def test_no_uzbek_text_leaks_in_ru():
    """RU tilida barcha loans_* qiymatlari Cyrillic bo'lishi kerak (Latin yo'q)."""
    UZBEK_MARKERS = ["Qarz", "Olgan", "Bergan", "Holat", "Muddat", "Jarayon"]
    for key in REQUIRED_KEYS:
        ru_val = TRANSLATIONS['ru'][key]
        for marker in UZBEK_MARKERS:
            assert marker not in ru_val, f"RU '{key}' ichida Uzbek matn topildi: '{marker}' in '{ru_val}'"
    print("  [OK] test_no_uzbek_text_leaks_in_ru")


def test_get_text_format_args_work():
    """t('loans_total_prefix', '5000') kabi format args ishlashi kerak."""
    for lang in LANGS:
        result = get_text(lang, 'loans_total_prefix', '5,000')
        assert '5,000' in result, f"{lang}: format arg almashishi ishlamadi: '{result}'"
        result = get_text(lang, 'loans_taken_section', 7)
        assert '7' in result, f"{lang}: count format args ishlamadi: '{result}'"
    print("  [OK] test_get_text_format_args_work")


def test_msg_added_with_three_args():
    """t('loans_msg_added', counterparty, amount, currency) format ishlashi kerak."""
    for lang in LANGS:
        result = get_text(lang, 'loans_msg_added', 'Bank XYZ', '5,000', 'USD')
        assert 'Bank XYZ' in result and '5,000' in result and 'USD' in result, \
            f"{lang}: msg_added format args ishlamadi: '{result}'"
    print("  [OK] test_msg_added_with_three_args")


if __name__ == '__main__':
    print("Running /loans i18n regression tests...")
    try:
        test_all_keys_exist_in_all_languages()
        test_no_uzbek_text_leaks_in_en()
        test_no_uzbek_text_leaks_in_ru()
        test_get_text_format_args_work()
        test_msg_added_with_three_args()
        print("\nAll tests passed.")
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
