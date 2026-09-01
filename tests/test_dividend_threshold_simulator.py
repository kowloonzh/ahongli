import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "simulate_dividend_threshold.py"


def load_module():
    spec = importlib.util.spec_from_file_location("simulate_dividend_threshold", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DividendThresholdSimulatorTest(unittest.TestCase):
    def test_simulation_output_is_namespaced_and_ratio_uses_requested_threshold(self):
        self.assertTrue(SCRIPT.exists())
        module = load_module()
        frame = pd.DataFrame(
            {
                "trade_date": ["20260101", "20260102", "20260103", "20260104"],
                "dv_ttm": [2.4, 2.5, 3.0, 1.9],
            }
        )

        profile = module.yield_threshold_profile(frame, 2.5)
        output = module.simulation_output_dir("20260831", 2.5)

        self.assertEqual(profile["threshold_ratio"], 0.5)
        self.assertEqual(output.name, "20260831_sim_yield_2_5pct")
        self.assertNotEqual(output, module.runner.output_dir("20260831"))


if __name__ == "__main__":
    unittest.main()
