"""Loans (Oldi-Berdi) route."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_all_loans, get_loan_summary

bp = Blueprint('loans', __name__)


@bp.route('/loans')
@login_required
def loans_page():
    loans = get_all_loans()
    return render_page('loans', 'loans.html',
        loans=loans,
        summary=get_loan_summary(),
        taken=[l for l in loans if l['loan_type'] == 'olgan'],
        given=[l for l in loans if l['loan_type'] == 'bergan'],
    )
