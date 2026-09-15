import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "simulate_insurance_scoring.py"
    spec = importlib.util.spec_from_file_location("insurance_scoring_simulation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_future_dps_is_only_an_outcome_label():
    module = load_module()
    records = [{
        "ts_code": "601318.SH", "period": "20241231", "report_type": "annual",
        "available_date": "20250320", "data_quality": "normal",
        "group_core_solvency_ratio": 160, "group_comprehensive_solvency_ratio": 190,
        "material_subsidiaries_json": '[{"reporting_entity":"寿险","core_solvency_ratio":120,"comprehensive_solvency_ratio":170}]',
        "stress_scenarios_json": '[{"reporting_entity":"保险集团","core_change_pct_points":-5,"comprehensive_change_pct_points":-6}]',
        "operating_profit_parent": 100, "operating_profit_payout_ratio": 0.35,
        "subsidiary_dividend_coverage": 1.1, "ending_available_funds_coverage": 1.4,
    }]
    rows = module.annual_point_in_time_rows(records, {"601318.SH": {"2024": 2.5, "2025": 2.0}})
    assert rows[0]["gate_status"] == "passed"
    assert rows[0]["subsequent_dps_cut"] is True
    assert rows[0]["as_of_date"] == "20250320"
    scores = module.build_research_score_rows(rows, [{"ts_code": "601318.SH"}])
    assert scores[0]["research_score"] is None
