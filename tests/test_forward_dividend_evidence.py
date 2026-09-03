import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ForwardDividendEvidenceTest(unittest.TestCase):
    def test_document_cache_uses_announcement_id_and_records_sha256(self):
        module = load_script("document_cache")
        with tempfile.TemporaryDirectory() as tmp:
            result = module.store_document(
                directory=Path(tmp),
                announcement_id="ANN-001",
                logical_role="annual",
                suffix=".pdf",
                content=b"original report bytes",
            )

            self.assertEqual(result["source_file"], "ANN-001-annual.pdf")
            self.assertEqual(len(result["sha256"]), 64)
            self.assertEqual(result["cache_status"], "stored")
            self.assertEqual((Path(tmp) / result["source_file"]).read_bytes(), b"original report bytes")

    def test_document_cache_reuses_identical_content_without_replacing_file(self):
        module = load_script("document_cache")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = module.store_document(
                directory=root, announcement_id="ANN-REUSE", logical_role="annual",
                suffix=".pdf", content=b"same bytes",
            )
            path = root / first["source_file"]
            before = path.stat().st_mtime_ns

            second = module.store_document(
                directory=root, announcement_id="ANN-REUSE", logical_role="annual",
                suffix=".pdf", content=b"same bytes",
            )

            self.assertEqual(second["cache_status"], "reused")
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_document_cache_detects_corrupted_cached_content(self):
        module = load_script("document_cache")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stored = module.store_document(
                directory=root,
                announcement_id="ANN-002",
                logical_role="policy",
                suffix=".pdf",
                content=b"trusted bytes",
            )
            path = root / stored["source_file"]
            path.write_bytes(b"corrupted bytes")

            self.assertFalse(module.validate_document(path, stored["sha256"]))

    def test_atomic_text_write_replaces_complete_file_content(self):
        module = load_script("document_cache")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text("old", encoding="utf-8")

            module.atomic_write_text(path, "new complete content")

            self.assertEqual(path.read_text(encoding="utf-8"), "new complete content")
            self.assertEqual(list(Path(tmp).glob(".manifest.json.*")), [])

    def test_announcement_titles_are_classified_by_economic_role(self):
        module = load_script("announcement_resolver")

        self.assertEqual(module.classify_announcement_title("公司2025年年度报告"), "annual")
        self.assertEqual(module.classify_announcement_title("公司2026年半年度报告"), "interim")
        self.assertEqual(module.classify_announcement_title("未来三年股东分红回报规划"), "dividend_policy")
        self.assertEqual(module.classify_announcement_title("2025年度利润分配预案"), "dividend_plan")
        self.assertEqual(module.classify_announcement_title("2025年度权益分派实施公告"), "implementation")

    def test_announcement_resolution_excludes_sources_after_cutoff(self):
        module = load_script("announcement_resolver")
        announcements = [
            {
                "announcementId": "A1",
                "announcementTitle": "公司2025年年度报告",
                "announcementTime": "2026-09-01 10:00:00",
                "adjunctUrl": "finalpage/2026-09-01/A1.PDF",
            },
            {
                "announcementId": "A2",
                "announcementTitle": "公司2026年半年度报告",
                "announcementTime": "2026-09-03 10:00:00",
                "adjunctUrl": "finalpage/2026-09-03/A2.PDF",
            },
        ]

        result = module.resolve_announcements(announcements, cutoff_at="20260902")

        self.assertEqual([row["announcement_id"] for row in result], ["A1"])
        self.assertEqual(result[0]["logical_role"], "annual")
        self.assertEqual(result[0]["available_date"], "20260901")
        self.assertEqual(result[0]["announcement_date"], "20260901")
        self.assertEqual(result[0]["source_url"], "https://static.cninfo.com.cn/finalpage/2026-09-01/A1.PDF")

    def test_revised_source_records_which_earlier_source_it_supersedes(self):
        module = load_script("announcement_resolver")
        announcements = [
            {
                "announcementId": "P1",
                "announcementTitle": "公司2025年度利润分配方案",
                "announcementTime": "2026-04-01 10:00:00",
            },
            {
                "announcementId": "P2",
                "announcementTitle": "公司2025年度利润分配方案（修订稿）",
                "announcementTime": "2026-04-02 10:00:00",
            },
        ]

        result = module.resolve_announcements(announcements, cutoff_at="20260902")

        self.assertEqual(result[1]["supersedes_source_id"], "P1")
        self.assertEqual(result[0]["superseded_by_source_id"], "P2")

    def test_forward_stock_map_resolves_every_company_in_current_formal_top10(self):
        module = load_script("cninfo_client")
        stocks = module.load_stock_maps(
            ROOT / "assets" / "stocks.json",
            ROOT / "assets" / "forward_stocks.json",
        )
        client = module.CnInfoClient(stocks=stocks)

        expected = {
            "601318", "600900", "600036", "601077", "600919",
            "601229", "601398", "600941", "601728", "601939",
        }
        resolved = {code for code in expected if client.resolve_stock(code) is not None}

        self.assertEqual(resolved, expected)

    def test_cninfo_query_paginates_with_resolved_org_id(self):
        module = load_script("cninfo_client")
        client = module.CnInfoClient(
            stocks={"szse": {"600900": {"orgId": "ORG-900", "zwjc": "长江电力"}}}
        )
        pages = []

        def fetch_page(payload):
            pages.append(dict(payload))
            page = payload["pageNum"]
            return {
                "hasMore": page == 1,
                "announcements": [{"announcementId": f"A{page}"}],
            }

        result = client.query_announcements(
            "600900.SH",
            start_date="20260101",
            end_date="20260902",
            searchkey="分红",
            fetch_page=fetch_page,
        )

        self.assertEqual([row["announcementId"] for row in result], ["A1", "A2"])
        self.assertEqual([page["pageNum"] for page in pages], [1, 2])
        self.assertEqual(pages[0]["stock"], "600900,ORG-900")
        self.assertEqual(pages[0]["seDate"], "2026-01-01~2026-09-02")

    def test_cninfo_http_fetcher_posts_to_official_query_endpoint(self):
        module = load_script("cninfo_client")
        observed = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"hasMore": False, "announcements": []}

        class Client:
            def __init__(self, **kwargs):
                observed["client_kwargs"] = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, data):
                observed["url"] = url
                observed["data"] = data
                return Response()

        module.httpx = types.SimpleNamespace(Client=Client)
        client = module.CnInfoClient(stocks={})

        result = client.build_page_fetcher()({"pageNum": 1})

        self.assertEqual(result, {"hasMore": False, "announcements": []})
        self.assertEqual(observed["url"], "https://www.cninfo.com.cn/new/hisAnnouncement/query")

    def test_cninfo_http_fetcher_retries_transient_request_failures_three_times(self):
        module = load_script("cninfo_client")
        observed = {"attempts": 0}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"hasMore": False, "announcements": []}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, data):
                observed["attempts"] += 1
                if observed["attempts"] < 3:
                    raise OSError("temporary network failure")
                return Response()

        module.httpx = types.SimpleNamespace(Client=Client)
        client = module.CnInfoClient(stocks={})

        result = client.build_page_fetcher()({"pageNum": 1})

        self.assertEqual(result["announcements"], [])
        self.assertEqual(observed["attempts"], 3)

    def test_cninfo_document_download_returns_original_bytes(self):
        module = load_script("cninfo_client")
        observed = {}

        class Response:
            content = b"original pdf"

            def raise_for_status(self):
                return None

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url):
                observed["url"] = url
                return Response()

        module.httpx = types.SimpleNamespace(Client=Client)
        client = module.CnInfoClient(stocks={})

        result = client.download_bytes("https://static.cninfo.com.cn/source.pdf")

        self.assertEqual(result, b"original pdf")
        self.assertEqual(observed["url"], "https://static.cninfo.com.cn/source.pdf")

    def test_evidence_preparation_only_downloads_formal_selected_rows(self):
        module = load_script("prepare_forward_dividend_evidence")
        queried = []

        class Client:
            def resolve_stock(self, code):
                return {"stock_code": code.split(".")[0], "market": "szse", "zwjc": "测试公司"}

            def query_announcements(self, code, **kwargs):
                queried.append(code)
                return [
                    {
                        "announcementId": "A1",
                        "announcementTitle": "测试公司2025年年度报告",
                        "announcementTime": "2026-03-20 10:00:00",
                        "adjunctUrl": "finalpage/A1.PDF",
                        "adjunctType": "PDF",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmp:
            result = module.prepare_forward_evidence(
                top_rows=[
                    {"rank": 1, "ts_code": "600900.SH", "name": "长江电力", "selected": "是"},
                    {"rank": 2, "ts_code": "600001.SH", "name": "未入选", "selected": "否"},
                ],
                run_date="20260902",
                evidence_root=Path(tmp),
                client=Client(),
                download_bytes=lambda url: b"pdf bytes",
            )

            self.assertEqual(set(queried), {"600900.SH"})
            self.assertEqual([row["ts_code"] for row in result], ["600900.SH"])
            manifest = Path(tmp) / "600900.SH" / "manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["documents"][0]["source_final_url"], payload["documents"][0]["source_url"])
            self.assertTrue(payload["documents"][0]["retrieved_at"].endswith("+00:00"))
            self.assertEqual(payload["documents"][0]["cache_status"], "stored")
            self.assertFalse((Path(tmp) / "600001.SH").exists())

    def test_one_company_download_failure_does_not_abort_other_top10_evidence(self):
        module = load_script("prepare_forward_dividend_evidence")

        class Client:
            def resolve_stock(self, code):
                return {"stock_code": code.split(".")[0], "market": "szse", "zwjc": code}

            def query_announcements(self, code, **kwargs):
                if code == "600002.SH":
                    raise OSError("one company unavailable")
                return [{
                    "announcementId": "GOOD-1", "announcementTitle": "公司2025年年度报告",
                    "announcementTime": "2026-03-20 10:00:00", "adjunctUrl": "GOOD-1.PDF", "adjunctType": "PDF",
                }]

        with tempfile.TemporaryDirectory() as tmp:
            result = module.prepare_forward_evidence(
                top_rows=[
                    {"rank": 1, "ts_code": "600001.SH", "name": "正常公司", "selected": "是"},
                    {"rank": 2, "ts_code": "600002.SH", "name": "失败公司", "selected": "是"},
                ],
                run_date="20260902",
                evidence_root=Path(tmp),
                client=Client(),
                download_bytes=lambda url: b"pdf bytes",
            )

            self.assertEqual([row["status"] for row in result], ["normal", "failed"])
            self.assertIn("one company unavailable", result[1]["reason"])
            self.assertTrue((Path(tmp) / "600001.SH" / "manifest.json").exists())
            self.assertTrue((Path(tmp) / "600002.SH" / "manifest.json").exists())
            parser = load_script("parse_forward_dividend_evidence")
            failed = parser.parse_company_evidence(
                Path(tmp) / "600002.SH" / "manifest.json",
                page_provider=lambda *_: [],
            )
            self.assertEqual(failed["stage_status"], "failed")
            self.assertIn("one company unavailable", failed["stage_reason"])

    def test_evidence_query_uses_targeted_categories_and_deduplicates_sources(self):
        module = load_script("prepare_forward_dividend_evidence")
        observed = []

        class Client:
            def query_announcements(self, code, **kwargs):
                observed.append((kwargs.get("category"), kwargs.get("searchkey")))
                return [{"announcementId": "SAME"}, {"announcementId": f"A{len(observed)}"}]

        result = module.collect_candidate_announcements(
            Client(),
            "600900.SH",
            start_date="20200101",
            end_date="20260902",
        )

        self.assertIn(("category_ndbg_szsh", ""), observed)
        self.assertIn(("category_bndbg_szsh", ""), observed)
        self.assertIn(("", "分红回报规划"), observed)
        self.assertIn(("", "权益分派"), observed)
        self.assertEqual(sum(row["announcementId"] == "SAME" for row in result), 1)

    def test_source_selection_keeps_latest_three_full_annual_reports(self):
        module = load_script("prepare_forward_dividend_evidence")
        sources = [
            {
                "announcement_id": f"R{year}",
                "logical_role": "annual",
                "source_title": f"公司{year}年年度报告",
                "available_at": f"{year + 1}-03-20T10:00:00+08:00",
                "adjunct_type": "PDF",
                "superseded_by_source_id": "",
            }
            for year in [2021, 2022, 2023, 2024, 2025]
        ]

        result = module._select_sources(sources)

        self.assertEqual(
            [row["announcement_id"] for row in result],
            ["R2023", "R2024", "R2025"],
        )

    def test_annual_source_selection_deduplicates_language_versions_by_fiscal_year(self):
        module = load_script("prepare_forward_dividend_evidence")
        sources = [
            {"announcement_id": "R23", "logical_role": "annual", "source_title": "公司2023年年度报告", "available_at": "2024-03-20T10:00:00+08:00", "adjunct_type": "PDF", "superseded_by_source_id": ""},
            {"announcement_id": "R24H", "logical_role": "annual", "source_title": "公司H股公告-2024年年度报告", "available_at": "2025-03-20T10:00:00+08:00", "adjunct_type": "PDF", "superseded_by_source_id": ""},
            {"announcement_id": "R25H", "logical_role": "annual", "source_title": "公司H股公告-2025年年度报告", "available_at": "2026-03-21T10:00:00+08:00", "adjunct_type": "PDF", "superseded_by_source_id": ""},
            {"announcement_id": "R25A", "logical_role": "annual", "source_title": "公司2025年度报告", "available_at": "2026-03-20T10:00:00+08:00", "adjunct_type": "PDF", "superseded_by_source_id": ""},
        ]

        result = module._select_sources(sources)

        self.assertEqual([row["announcement_id"] for row in result], ["R23", "R24H", "R25A"])

    def test_dividend_plan_and_implementation_merge_into_one_economic_event(self):
        module = load_script("parse_forward_dividend_evidence")
        plan = module.parse_dividend_event(
            "公司拟以总股本1000000000股为基数，每10股派发现金红利5.00元（含税）。",
            source={"announcement_id": "P1", "logical_role": "dividend_plan", "source_title": "2025年度利润分配方案"},
            ts_code="600001.SH",
            fiscal_period="20251231",
        )
        implementation = module.parse_dividend_event(
            "公司以总股本1000000000股为基数，每10股派发现金红利5.00元（含税）。股权登记日2026年6月9日，除权除息日2026年6月10日。",
            source={
                "announcement_id": "I1",
                "logical_role": "implementation",
                "source_title": "2025年度权益分派实施公告",
                "available_at": "2026-06-01T10:00:00+08:00",
            },
            ts_code="600001.SH",
            fiscal_period="20251231",
        )

        result = module.merge_dividend_events([plan, implementation])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["cash_dividend_per_share_pre_tax"], 0.50)
        self.assertEqual(result[0]["status"], "implementation")
        self.assertEqual(result[0]["distribution_phase"], "final")
        self.assertEqual(result[0]["record_date"], "20260609")
        self.assertEqual(result[0]["ex_dividend_date"], "20260610")
        self.assertEqual(result[0]["available_date"], "20260601")
        self.assertEqual(result[0]["evidence_source_ids"], ["P1", "I1"])

    def test_same_announcement_keeps_declared_dps_not_lower_diluted_reference(self):
        module = load_script("parse_forward_dividend_evidence")
        declared = module.parse_dividend_event(
            "每股派发现金股息人民币1.75元（含税）。",
            source={"announcement_id": "SAME", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告"},
            ts_code="601318.SH",
            fiscal_period="20251231",
        )
        diluted = module.parse_dividend_event(
            "每股派发现金股息人民币1.575元（含税）。",
            source={"announcement_id": "SAME", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告"},
            ts_code="601318.SH",
            fiscal_period="20251231",
        )

        result = module.merge_dividend_events([declared, diluted])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["cash_dividend_per_share_pre_tax"], 1.75)
        self.assertEqual(result[0]["evidence_source_ids"], ["SAME"])

    def test_policy_parser_keeps_ratio_period_and_raw_evidence(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "2026年至2030年，公司每年现金分红比例不低于当年归母净利润的70%。"

        result = module.parse_policy_facts(
            text,
            source={"announcement_id": "POL-1", "source_file": "POL-1.pdf", "page": 3},
            ts_code="600900.SH",
        )

        self.assertEqual(result["official_payout_floor"]["value"], 0.70)
        self.assertEqual(result["official_payout_floor"]["valid_from"], 2026)
        self.assertEqual(result["official_payout_floor"]["valid_to"], 2030)
        self.assertEqual(result["official_payout_floor"]["source_document_id"], "POL-1")
        self.assertEqual(result["official_payout_floor"]["raw_evidence"], text)

    def test_three_year_telecom_policy_resolves_explicit_validity_window(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "公司从2024年起，三年内以现金方式分配的利润逐步提升至当年股东应占利润的75%以上。"

        result = module.parse_policy_facts(
            text,
            source={"announcement_id": "TEL-POLICY", "source_file": "TEL.pdf", "page": 68},
            ts_code="601728.SH",
        )

        floor = result["official_payout_floor"]
        self.assertEqual(floor["value"], 0.75)
        self.assertEqual((floor["valid_from"], floor["valid_to"]), (2024, 2026))

    def test_historical_payout_increase_without_future_window_is_not_a_policy_floor(self):
        module = load_script("parse_forward_dividend_evidence")

        result = module.parse_policy_facts(
            "2025年经营稳中向好，现金分红率提升至35%。",
            source={"announcement_id": "CMB-ANNUAL", "source_file": "CMB.pdf", "page": 5},
            ts_code="600036.SH",
        )

        self.assertNotIn("official_payout_floor", result)

    def test_financial_fact_parser_preserves_units_period_and_source(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "归属于上市公司股东的净利润（元） 35,000,000,000.00\n"
            "期末总股本（股） 24,470,000,000"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "RPT-1", "source_file": "RPT-1.pdf", "page": 12},
            ts_code="600900.SH",
            period="20260630",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 35_000_000_000.0)
        self.assertEqual(result["net_profit_parent"]["unit"], "CNY")
        self.assertEqual(result["net_profit_parent"]["period"], "20260630")
        self.assertEqual(result["total_shares"]["value"], 24_470_000_000)
        self.assertEqual(result["total_shares"]["unit"], "share")
        self.assertEqual(result["total_shares"]["source_document_id"], "RPT-1")

    def test_interim_parser_keeps_prior_comparable_profit_separately(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "归属于上市公司股东的净利润（元） 17,000,000,000.00 15,000,000,000.00"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "H1", "source_file": "H1.pdf", "page": 8},
            ts_code="600900.SH",
            period="20260630",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 17_000_000_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 15_000_000_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["period"], "20250630")

    def test_profit_parser_does_not_treat_payout_percent_as_prior_profit(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "按照2026年中期归属于公司股东的净利润人民币195.88亿元的75%向全体股东分配股息，"
            "合计人民币约146.96亿元。"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "TEL-H1", "source_file": "TEL-H1.pdf", "page": 2},
            ts_code="601728.SH",
            period="20260630",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 19_588_000_000.0)
        self.assertNotIn("net_profit_parent_prior_comparable", result)

    def test_insurance_operating_profit_is_a_separate_fact(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "归属于母公司股东的营运利润（元） 120,000,000,000.00 110,000,000,000.00"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "INS-1", "source_file": "INS-1.pdf", "page": 9},
            ts_code="601318.SH",
            period="20260630",
        )

        self.assertEqual(result["operating_profit_parent"]["value"], 120_000_000_000.0)
        self.assertEqual(result["operating_profit_parent_prior_comparable"]["value"], 110_000_000_000.0)

    def test_real_report_narrative_profit_in_yuan_is_parsed(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "合并报表归属于上市公司股东的净利润为 34,502,809,176.39 元。"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "REAL-1", "page": 39},
            ts_code="600900.SH",
            period="20251231",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 34_502_809_176.39)
        self.assertEqual(result["net_profit_parent"]["unit"], "CNY")

    def test_real_report_table_converts_yi_yuan_and_keeps_comparative(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "归属于上市公司股东的净利润（亿元） 345.03 324.96 272.39"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "REAL-2", "page": 76},
            ts_code="600900.SH",
            period="20251231",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 34_503_000_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 32_496_000_000.0)

    def test_real_bank_table_uses_page_level_million_yuan_unit(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "（人民币百万元，特别注明除外） 2025年 2024年 2023年\n"
            "归属于本行股东的净利润 150,181 148,391 1.21 146,602"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "BANK-1", "page": 13},
            ts_code="600036.SH",
            period="20251231",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 150_181_000_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 148_391_000_000.0)

    def test_real_insurance_narrative_does_not_treat_yoy_percent_as_prior_profit(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "归属于母公司股东的营运利润 1,344.15 亿元，同比增长 10.3%；"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "INS-REAL", "page": 5},
            ts_code="601318.SH",
            period="20251231",
        )

        self.assertEqual(result["operating_profit_parent"]["value"], 134_415_000_000.0)
        self.assertNotIn("operating_profit_parent_prior_comparable", result)

    def test_real_narrative_total_share_format_is_parsed(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "截至2025年12月31日，本公司普通股总股本25,219,845,601股。"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "SHARE-REAL", "page": 45},
            ts_code="600036.SH",
            period="20251231",
        )

        self.assertEqual(result["total_shares"]["value"], 25_219_845_601)

    def test_real_dividend_base_share_format_is_parsed(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "本行拟以356,406,257,089股普通股为基数向普通股股东派发现金股息。"

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "BASE-SHARE", "page": 17},
            ts_code="601398.SH",
            period="20251231",
        )

        self.assertEqual(result["total_shares"]["value"], 356_406_257_089)

    def test_real_multiline_income_statement_metric_uses_following_value_row(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "单位：元 币种：人民币\n"
            "1.归属于母公司股东的净利润\n"
            "14,755,694,774.53   13,056,352,473.55\n"
            "（净亏损以负号填列）"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "MULTILINE", "page": 88},
            ts_code="600900.SH",
            period="20260630",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 14_755_694_774.53)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 13_056_352_473.55)

    def test_real_bank_page_supports_thousand_yuan_unit(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "单位：千元 币种：人民币\n"
            "归属于母公司股东的净利润 21,875,647 20,238,459 8.09"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "THOUSAND", "page": 10},
            ts_code="600919.SH",
            period="20260630",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 21_875_647_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 20_238_459_000.0)

    def test_traditional_chinese_bank_report_labels_are_supported(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "（人民幣百萬元，特別注明除外）\n"
            "歸屬於本行股東的淨利潤 338,906 335,577 0.99 332,653"
        )

        result = module.parse_financial_facts(
            text,
            source={"announcement_id": "H-BANK", "page": 12},
            ts_code="601939.SH",
            period="20251231",
        )

        self.assertEqual(result["net_profit_parent"]["value"], 338_906_000_000.0)
        self.assertEqual(result["net_profit_parent_prior_comparable"]["value"], 335_577_000_000.0)

    def test_real_per_share_cash_dividend_format_is_parsed(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "本次利润分配以总股本25,219,845,601股为基数，每股派发现金股息人民币1.522元（含税）。"

        result = module.parse_dividend_event(
            text,
            source={"announcement_id": "DIV-REAL", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告"},
            ts_code="600036.SH",
            fiscal_period="20251231",
        )

        self.assertEqual(result["cash_dividend_per_share_pre_tax"], 1.522)

    def test_full_year_total_is_not_mislabeled_as_incremental_final_dividend(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "全年现金股息总额人民币1,016.84亿元（每股现金股息人民币0.3887元（含税））；"
            "扣除中期现金股息每股人民币0.1858元后，向股东派发末期现金股息。"
        )

        result = module.parse_dividend_event(
            text,
            source={
                "announcement_id": "CCB-IMPL",
                "logical_role": "implementation",
                "source_title": "建设银行2025年度A股分红派息实施公告",
            },
            ts_code="601939.SH",
            fiscal_period="20251231",
        )

        self.assertEqual(result["cash_dividend_per_share_pre_tax"], 0.3887)
        self.assertEqual(result["distribution_phase"], "full_year")

    def test_mixed_share_class_announcement_uses_a_share_cny_dps(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "H股每股派发现金股息人民币1.20元；"
            "A股每股派发现金股息人民币1.00元。"
        )

        result = module.parse_dividend_event(
            text,
            source={"announcement_id": "MIXED", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告"},
            ts_code="600036.SH",
            fiscal_period="20251231",
        )

        self.assertEqual(result["cash_dividend_per_share_pre_tax"], 1.00)
        self.assertEqual(result["share_class"], "A")
        self.assertEqual(result["currency"], "CNY")

    def test_a_share_dividend_is_found_after_non_dividend_a_share_mentions(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "A股上市信息；H股每股派发现金股息1.20港元；A股每股派发现金股息人民币1.00元。"

        result = module.parse_dividend_event(
            text,
            source={"announcement_id": "MIXED-LATE", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告"},
            ts_code="600036.SH",
            fiscal_period="20251231",
        )

        self.assertEqual(result["cash_dividend_per_share_pre_tax"], 1.00)

    def test_h_share_only_dividend_is_not_accepted_as_a_share_evidence(self):
        module = load_script("parse_forward_dividend_evidence")

        with self.assertRaisesRegex(ValueError, "A-share"):
            module.parse_dividend_event(
                "H股每股派发现金股息人民币1.20元。",
                source={"announcement_id": "H-ONLY", "logical_role": "implementation", "source_title": "H股股息公告"},
                ts_code="600036.SH",
                fiscal_period="20251231",
            )

    def test_real_implementation_summary_parses_dps_and_slash_date_row(self):
        module = load_script("parse_forward_dividend_evidence")
        text = (
            "A股每股现金红利0.79元（含税）\n"
            "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日\n"
            "Ａ股 2026/7/16 － 2026/7/17 2026/7/17"
        )

        result = module.parse_dividend_event(
            text,
            source={"announcement_id": "DATE-ROW", "logical_role": "implementation", "source_title": "2025年度权益分派实施公告", "available_at": "2026-07-10T10:00:00+08:00"},
            ts_code="600900.SH",
            fiscal_period="20251231",
        )

        self.assertEqual(result["cash_dividend_per_share_pre_tax"], 0.79)
        self.assertEqual(result["record_date"], "20260716")
        self.assertEqual(result["ex_dividend_date"], "20260717")
        self.assertEqual(result["payment_date"], "20260717")

    def test_company_parser_writes_traceable_facts_and_events(self):
        module = load_script("parse_forward_dividend_evidence")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "ts_code": "600900.SH",
                "company_name": "长江电力",
                "run_date": "20260902",
                "documents": [
                    {
                        "announcement_id": "R1",
                        "logical_role": "annual",
                        "source_title": "长江电力2025年年度报告",
                        "source_file": "reports/R1-annual.pdf",
                    },
                    {
                        "announcement_id": "H1",
                        "logical_role": "interim",
                        "source_title": "长江电力2026年半年度报告",
                        "source_file": "reports/H1-interim.pdf",
                    },
                    {
                        "announcement_id": "P1",
                        "logical_role": "dividend_policy",
                        "source_title": "2026年至2030年股东回报规划",
                        "source_file": "announcements/P1-policy.pdf",
                    },
                    {
                        "announcement_id": "D1",
                        "logical_role": "dividend_plan",
                        "source_title": "2025年度利润分配方案",
                        "source_file": "announcements/D1-plan.pdf",
                    },
                ],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
            texts = {
                "R1": "归属于上市公司股东的净利润（元） 35,000,000,000.00\n期末总股本（股） 24,470,000,000",
                "H1": (
                    "归属于上市公司股东的净利润（元） 17,000,000,000.00 15,000,000,000.00\n"
                    "归属于母公司股东的营运利润（元） 18,000,000,000.00 16,000,000,000.00"
                ),
                "P1": "2026年至2030年，公司每年现金分红比例不低于当年归母净利润的70%。",
                "D1": "以总股本24470000000股为基数，每10股派发现金红利10.00元（含税）。",
            }

            result = module.parse_company_evidence(
                manifest_path,
                page_provider=lambda document, _: [{"page": 1, "text": texts[document["announcement_id"]]}],
            )

            fact_types = {fact["fact_type"] for fact in result["normalized_facts"]}
            self.assertEqual(
                fact_types,
                {
                    "net_profit_parent", "net_profit_parent_prior_comparable",
                    "operating_profit_parent", "operating_profit_parent_prior_comparable",
                    "total_shares", "official_payout_floor",
                },
            )
            self.assertEqual(len(result["dividend_events"]), 1)
            self.assertTrue((root / "forecast-evidence.json").exists())

    def test_formal_future_policy_inside_annual_report_is_extracted(self):
        module = load_script("parse_forward_dividend_evidence")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "ts_code": "600900.SH",
                "run_date": "20260902",
                "documents": [{
                    "announcement_id": "R-POLICY",
                    "logical_role": "annual",
                    "source_title": "长江电力2025年年度报告",
                    "source_file": "reports/R-POLICY-annual.pdf",
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            result = module.parse_company_evidence(
                path,
                page_provider=lambda *_: [{
                    "page": 20,
                    "text": "2026年至2030年，每年度利润分配按不低于当年归属于母公司股东净利润的70%进行现金分红。",
                }],
            )

            policy = next(fact for fact in result["normalized_facts"] if fact["fact_type"] == "official_payout_floor")
            self.assertEqual(policy["value"], 0.70)
            self.assertEqual((policy["valid_from"], policy["valid_to"]), (2026, 2030))

    def test_company_parser_carries_statement_unit_across_pdf_page_boundary(self):
        module = load_script("parse_forward_dividend_evidence")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "ts_code": "600900.SH",
                "run_date": "20260902",
                "documents": [{
                    "announcement_id": "CROSS-PAGE",
                    "logical_role": "interim",
                    "source_title": "长江电力2026年半年度报告",
                    "source_file": "reports/CROSS-PAGE.pdf",
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            pages = [
                {"page": 50, "text": "合并利润表\n单位：元 币种：人民币"},
                {"page": 51, "text": "归属于母公司股东的净利润\n14,755,694,774.53 13,056,352,473.55"},
            ]

            result = module.parse_company_evidence(path, page_provider=lambda *_: pages)

            fact = next(
                item
                for item in result["normalized_facts"]
                if item["fact_type"] == "net_profit_parent" and item["period"] == "20260630"
            )
            self.assertEqual(fact["value"], 14_755_694_774.53)
            self.assertEqual(fact["source_page_or_location"], 51)

    def test_distinct_page_facts_have_distinct_stable_fact_ids(self):
        module = load_script("parse_forward_dividend_evidence")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "ts_code": "600001.SH",
                "run_date": "20260902",
                "documents": [{
                    "announcement_id": "MULTI-FACT",
                    "logical_role": "annual",
                    "source_title": "公司2025年年度报告",
                    "source_file": "reports/MULTI-FACT.pdf",
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            pages = [
                {"page": 1, "text": "归属于上市公司股东的净利润（亿元） 100.00"},
                {"page": 2, "text": "归属于上市公司股东的净利润（亿元） 25.00"},
            ]

            result = module.parse_company_evidence(path, page_provider=lambda *_: pages)
            profit_facts = [fact for fact in result["normalized_facts"] if fact["fact_type"] == "net_profit_parent"]

            self.assertEqual(len(profit_facts), 2)
            self.assertEqual(len({fact["fact_id"] for fact in profit_facts}), 2)

    def test_real_dividend_plan_payout_ratio_is_a_historical_fact(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "截至2025年12月31日，2025年度本公司现金分红比例为35.34%。"

        result = module.parse_historical_payout_fact(
            text,
            source={"announcement_id": "BANK-PLAN", "page": 49},
            ts_code="600036.SH",
            period="20251231",
        )

        self.assertEqual(result["value"], 0.3534)
        self.assertEqual(result["period"], "20251231")
        self.assertEqual(result["source_document_id"], "BANK-PLAN")

    def test_real_telecom_full_year_payout_ratio_is_a_historical_fact(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "2025年全年股息合计每股5.27港元，2025年全年派息率为75%。"

        result = module.parse_historical_payout_fact(
            text,
            source={"announcement_id": "TEL-PLAN", "page": 18},
            ts_code="600941.SH",
            period="20251231",
        )

        self.assertEqual(result["value"], 0.75)

    def test_official_stable_or_rising_payout_statement_becomes_next_year_floor(self):
        module = load_script("parse_forward_dividend_evidence")
        text = "2025年全年派息率为75%。公司充分保障股东权益，2026年派息率将稳中有升。"

        result = module.parse_policy_facts(
            text,
            source={"announcement_id": "TEL-POLICY", "page": 22},
            ts_code="600941.SH",
        )

        floor = result["official_payout_floor"]
        self.assertEqual(floor["value"], 0.75)
        self.assertEqual((floor["valid_from"], floor["valid_to"]), (2026, 2026))

    def test_dividend_plan_contributes_auditable_share_and_profit_facts(self):
        module = load_script("parse_forward_dividend_evidence")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "ts_code": "600036.SH",
                "run_date": "20260902",
                "documents": [{
                    "announcement_id": "PLAN-FACTS",
                    "logical_role": "dividend_plan",
                    "source_title": "2025年度利润分配方案",
                    "source_file": "announcements/PLAN-FACTS.pdf",
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            text = (
                "归属于本行普通股股东的净利润（亿元） 1,500.00\n"
                "截至2025年12月31日本公司普通股总股本25,219,845,601股。"
            )

            result = module.parse_company_evidence(
                path,
                page_provider=lambda *_: [{"page": 2, "text": text}],
            )

            fact_types = {fact["fact_type"] for fact in result["normalized_facts"]}
            self.assertIn("net_profit_parent", fact_types)
            self.assertIn("total_shares", fact_types)

    def test_extracted_pdf_text_keeps_one_based_page_locations(self):
        module = load_script("parse_forward_dividend_evidence")

        result = module.split_extracted_pages("第一页内容\f第二页内容\f")

        self.assertEqual(result, [
            {"page": 1, "text": "第一页内容"},
            {"page": 2, "text": "第二页内容"},
        ])

    def test_pdf_extraction_uses_pdftotext_layout_and_persists_page_json(self):
        module = load_script("parse_forward_dividend_evidence")
        observed = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "reports" / "R1-annual.pdf"
            source.parent.mkdir()
            source.write_bytes(b"pdf")

            def runner(args, **kwargs):
                observed["args"] = args
                Path(args[-1]).write_text("第一页\f第二页", encoding="utf-8")
                return types.SimpleNamespace(returncode=0)

            result = module.extract_document_pages(
                {"announcement_id": "R1", "logical_role": "annual", "source_file": "reports/R1-annual.pdf"},
                root,
                command_runner=runner,
            )

            self.assertEqual(observed["args"][:2], ["pdftotext", "-layout"])
            self.assertEqual([page["page"] for page in result], [1, 2])
            self.assertTrue((root / "extracted" / "R1-annual.pages.json").exists())


if __name__ == "__main__":
    unittest.main()
