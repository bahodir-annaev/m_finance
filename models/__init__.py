"""models package — re-exports every public symbol for backward compatibility."""
from .base import (
    get_db, init_db, get_setting, get_rate_for_date, get_current_usd_rate,
    get_record, update_record, delete_record, get_all_records,
    write_audit_log, get_open_fiscal_period, is_period_closed,
    resolve_entity_id, record_unresolved_import, apply_entity_resolution,
)
from .equipment import (
    get_general_equipment_monthly, get_personal_equipment_monthly, get_personal_license_monthly,
)
from .staff import (
    get_available_hours, count_staff_by_type, get_total_billable_hours,
    get_staff_billable_hours, get_admin_total_cost, get_total_overhead,
    get_admin_share_for_staff, get_overhead_share_for_staff,
    get_general_equipment_share_for_staff, calculate_hourly_rate, get_staff_kpi,
    snapshot_hours_rates, snapshot_period_allocations, close_fiscal_period,
    get_production_staff_rates,
)
from .projects import (
    get_staff_billable_hours_for_project, calculate_fx_gain_loss,
    get_earned_revenue, calculate_project_cost,
)
from .pricing import calculate_risk_score, pricing_estimate
from .milestones import (
    get_work_types, get_project_milestones, get_milestone_actuals,
    get_project_monthly_actuals, get_project_monthly_rollup,
    get_unassigned_actuals, suggest_phase_for_period, allocate_plan_to_months,
    add_milestone, delete_milestone, set_milestone_status,
    assign_hours_to_milestone, get_plan_overview,
    get_projects_on_course_summary, generate_milestones_from_pricing,
    set_milestone_staff, get_milestone_staff,
)
from .base import INCOME_TX_TYPES, INCOME_TX_SQL
from .transactions import get_cash_flow_by_month, get_payment_summary, update_transaction
from .loans import get_all_loans, get_loan_detail, get_loan_summary
from .dividends import get_all_dividends, get_dividend_summary, add_dividend
from .dashboard import (
    get_dashboard_data, get_burn_rate_and_runway, get_capacity_data, get_ar_aging,
)
from .import_export import analyze_excel_for_import, import_excel_data

__all__ = [
    'get_db', 'init_db', 'get_setting', 'get_rate_for_date', 'get_current_usd_rate',
    'get_record', 'update_record', 'delete_record', 'get_all_records',
    'write_audit_log', 'get_open_fiscal_period', 'is_period_closed',
    'resolve_entity_id', 'record_unresolved_import', 'apply_entity_resolution',
    'get_general_equipment_monthly', 'get_personal_equipment_monthly', 'get_personal_license_monthly',
    'get_available_hours', 'count_staff_by_type', 'get_total_billable_hours',
    'get_staff_billable_hours', 'get_admin_total_cost', 'get_total_overhead',
    'get_admin_share_for_staff', 'get_overhead_share_for_staff',
    'get_general_equipment_share_for_staff', 'calculate_hourly_rate', 'get_staff_kpi',
    'snapshot_hours_rates', 'snapshot_period_allocations', 'close_fiscal_period',
    'get_production_staff_rates',
    'get_staff_billable_hours_for_project', 'calculate_fx_gain_loss',
    'get_earned_revenue', 'calculate_project_cost',
    'calculate_risk_score', 'pricing_estimate',
    'get_work_types', 'get_project_milestones', 'get_milestone_actuals',
    'get_project_monthly_actuals', 'get_project_monthly_rollup',
    'get_unassigned_actuals', 'suggest_phase_for_period', 'allocate_plan_to_months',
    'add_milestone', 'delete_milestone', 'set_milestone_status',
    'assign_hours_to_milestone', 'get_plan_overview',
    'get_projects_on_course_summary', 'generate_milestones_from_pricing',
    'set_milestone_staff', 'get_milestone_staff',
    'INCOME_TX_TYPES', 'INCOME_TX_SQL',
    'get_cash_flow_by_month', 'get_payment_summary', 'update_transaction',
    'get_all_loans', 'get_loan_detail', 'get_loan_summary',
    'get_all_dividends', 'get_dividend_summary', 'add_dividend',
    'get_dashboard_data', 'get_burn_rate_and_runway', 'get_capacity_data', 'get_ar_aging',
    'analyze_excel_for_import', 'import_excel_data',
]
