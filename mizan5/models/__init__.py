"""Model package — re-exports the public surface of every submodule.

Controllers import from `models`, never from `models.<submodule>`, so an
internal reorganisation never touches a controller.
"""
from .base import (
    get_db, init_db, now_ts, today_str, period_of, period_end,
    asset_class_map, default_lifespan_for, ASSET_CLASS_SEED,
    get_setting, set_setting, get_rate_for_date, get_current_usd_rate,
    get_record, update_record, delete_record, get_all_records, get_lookup,
    write_audit_log, normalize_name, resolve_entity_id,
    get_period_status, is_period_closed, ensure_fiscal_period,
    create_fiscal_period, delete_fiscal_period, set_period_notes,
    DOC_TYPES, DOC_PREFIXES, ACCOUNT_KINDS, BALANCE_EPSILON,
)
from .ledger import (
    PostingError, post_entry, reverse_entry,
    get_accounts, get_account_by_code, account_id_for, account_map_all,
    pnl_section, natural_side, signed_balance,
    account_turnover, account_balance, balances_by_purpose,
    get_trial_balance, get_journal, get_account_ledger,
    balances_by_analytic, monthly_turnover, accounts_in_pool, verify_all_entries,
)
from .documents import (
    DocumentError, save_document, post_document, void_document, delete_draft,
    get_document, list_documents, count_documents, invoice_outstanding, open_invoices,
    DOCUMENT_SORTS,
    next_document_number, default_vat_rate, split_vat, add_vat,
    INVOICE_TYPES, CASH_TYPES,
)
from .posting import (
    build_entry_lines, cash_account_purpose, cash_account_for, assert_not_pooled,
)
from .bank_accounts import (
    list_bank_accounts, get_bank_account, save_bank_account, default_bank_account_id,
    bank_account_balances, assign_unassigned_lines, subdividable_accounts,
)
from .direction import (
    document_direction, document_directions, cash_turnover_by_direction,
    DIRECTIONS, CLIENT_PURPOSES, DIRECT_COST_PURPOSES, FINANCING_PURPOSES,
)
from .depreciation import (
    depreciation_schedule, depreciation_preview, depreciation_posted_entry,
    post_period_depreciation, reverse_period_depreciation,
    classify_asset, classify_register,
    DEPRECIATION_MEMO_PREFIX,
)
from .staff import (
    get_available_hours, available_hours_value, get_current_salary, salary_at,
    employer_burden, latest_period, billable_hours_by_staff,
    personal_equipment_monthly, personal_licenses_monthly, general_equipment_monthly,
    overhead_budget_monthly, ledger_overhead_monthly, overhead_monthly,
    admin_total_cost, allocation_base_name, compute_shares,
    calculate_hourly_rate, rate_context, get_production_staff_rates,
    get_rates_overview, snapshot_period_allocations, snapshot_hours_rates,
    get_period_snapshot, depreciation_reconciliation, get_staff_kpi,
)
from .payroll import (
    build_payroll_rows, payroll_totals, save_payroll, get_payroll_for_period,
    payroll_liabilities, build_remittance_lines, list_payroll_periods,
    period_bounds,
)
from .pricing import (
    calculate_risk_score, price_from_cost, get_target_margin,
    get_project_risk, save_project_risk, get_milestone_staff, set_milestone_staff,
    price_project_plan, apply_suggested_prices, freeze_project_plan,
    get_plan_baseline, quote_schedule, save_quote_to_project,
    RISK_CLIENT_TYPES, RISK_COMPLEXITIES,
)
from .milestones import (
    get_project_milestones, get_milestone_actuals, get_project_monthly_actuals,
    rollup_milestone_plan, allocate_plan_to_months, add_milestone,
    delete_milestone, set_milestone_status, assign_hours_to_milestone,
    assign_document_to_milestone, get_unassigned_actuals, suggest_phase_for_period,
    get_project_monthly_rollup,
    generate_milestone_schedule, plan_expense_total,
    month_first_day, month_last_day, STANDARD_SCHEDULE, MILESTONE_STATUSES,
)
from .projects import (
    list_projects, get_project, create_project, project_actuals,
    calculate_project_cost, get_budget_overview, get_portfolio_summary,
    get_projects_on_course_summary, get_top_projects, calculate_fx_gain_loss,
    budget_verdict, PROJECT_SORTS,
)
from .reports import (
    get_pnl, get_balance_sheet, get_cash_flow, cash_balance, cash_by_account,
    cash_account_ids, get_aging, aging_by_counterparty, get_vat_report,
    fx_position, post_fx_revaluation, close_period, reopen_period, list_periods,
    get_burn_rate, get_runway, get_capacity, get_dashboard, payment_method_summary,
)
from .loans import (
    list_loans, loan_summary, loan_balance, loan_documents, save_loan,
    sync_loan_status, post_loan_issue, record_loan_payment, principal_purpose,
)
from .import_nizam import (
    import_nizam_file, read_nizam_sheet, match_staff, parse_hhmm, NIZAM_MAP, NON_BILLABLE,
)

__all__ = [name for name in dir() if not name.startswith('_')]
