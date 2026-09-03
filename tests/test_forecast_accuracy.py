import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location("evaluate_forecast_accuracy", SCRIPTS / "evaluate_forecast_accuracy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForecastAccuracyTest(unittest.TestCase):
    def test_actual_dps_is_compared_with_versioned_forecast_range(self):
        module = load_module()
        result = module.evaluate_accuracy(
            forecasts=[{
                "ts_code": "600900.SH", "name": "长江电力", "forecast_fiscal_year": "2026",
                "forecast_fy_regular_dps_low": "1.00", "forecast_fy_regular_dps_base": "1.05",
                "forecast_fy_regular_dps_high": "1.10", "model_id": "regular_profit_policy_v1",
                "model_version": "1",
            }],
            actuals=[{"ts_code": "600900.SH", "fiscal_year": "2026", "actual_regular_dps": "1.08"}],
        )

        self.assertAlmostEqual(result["rows"][0]["absolute_error"], 0.03)
        self.assertAlmostEqual(result["rows"][0]["relative_error"], -0.03 / 1.08)
        self.assertTrue(result["rows"][0]["within_forecast_range"])
        self.assertEqual(result["summary"]["within_range_count"], 1)


if __name__ == "__main__":
    unittest.main()
