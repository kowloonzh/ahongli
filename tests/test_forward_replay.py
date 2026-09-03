import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForwardReplayTest(unittest.TestCase):
    def test_manifest_replays_forecast_from_hashed_inputs(self):
        runner = load_script("run_forward_dividend_analysis")
        replay = load_script("forward_replay")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            evidence_dir = out / "market_data/forward_dividend_evidence/600900.SH"
            evidence_dir.mkdir(parents=True)
            reports_dir = evidence_dir / "reports"
            reports_dir.mkdir()
            report_path = reports_dir / "R1-annual.pdf"
            report_path.write_bytes(b"original report")
            top = out / "hs300-dividend-top10-20260902.csv"
            with top.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "rank", "ts_code", "name", "industry", "selected", "dividend_score_total",
                    "current_price", "price_date", "roe", "latest_payout_ratio", "dps_cagr_5y",
                ])
                writer.writeheader()
                writer.writerow({
                    "rank": 1, "ts_code": "600900.SH", "name": "长江电力", "industry": "水力发电",
                    "selected": "是", "dividend_score_total": 74.58, "current_price": 28.4,
                    "price_date": "20260901", "roe": 17.13, "latest_payout_ratio": 0.7092,
                    "dps_cagr_5y": 0.05,
                })
            evidence = {"normalized_facts": [
                {"fact_id": "FY", "fact_type": "net_profit_parent", "period": "20251231", "value": 33.0},
                {"fact_id": "H1", "fact_type": "net_profit_parent", "period": "20260630", "value": 17.0},
                {"fact_id": "PRIOR", "fact_type": "net_profit_parent_prior_comparable", "period": "20250630", "value": 15.0},
                {"fact_id": "SHARES", "fact_type": "total_shares", "period": "20260630", "value": 24.47},
                {"fact_id": "POLICY", "fact_type": "official_payout_floor", "period": "20261231", "value": 0.70, "valid_from": 2026, "valid_to": 2030},
            ], "dividend_events": [], "source_documents": [{
                "announcement_id": "R1", "source_file": "reports/R1-annual.pdf",
                "sha256": hashlib.sha256(b"original report").hexdigest(),
            }]}
            (evidence_dir / "forecast-evidence.json").write_text(json.dumps(evidence), encoding="utf-8")

            result = runner.run_forward_analysis(
                run_date="20260902", top10_path=top, output_dir=out, skip_prepare=True,
            )
            manifest = result["outputs"]["replay_manifest"]
            verified = replay.replay_forward_analysis(manifest, verify_code=False)

            self.assertTrue(verified["replay_verified"])
            self.assertEqual(verified["row_count"], 1)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["code_commit"]), 40)
            self.assertEqual(len(payload["policy"]["sha256"]), 64)
            self.assertEqual(len(payload["inputs"]["top10_sha256"]), 64)
            self.assertEqual(len(payload["outputs"]["forward_csv_sha256"]), 64)
            self.assertEqual(
                payload["inputs"]["evidence"][0]["source_documents"][0]["sha256"],
                hashlib.sha256(b"original report").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
