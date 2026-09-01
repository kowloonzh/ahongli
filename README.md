# AHongli

AHongli 是一个面向沪深300的质量红利筛选 Skill。正式策略先检查五年股息率持续性，再执行结构化盈利、分红、现金流、审计、主营范围和银行专项硬门槛，最后按银行、其他金融和非金融三套模型分别评分。

本项目仅用于研究和数据整理，不构成投资建议。

## 实际运行示例（2026-09-01）

以下结果来自正式3%策略的一次完整运行，用于直观展示 Skill 的筛选、评分和审计能力。排名是特定日期的数据快照；成分股、财务数据和因子分位会变化，后续使用时应重新运行，不应把本表当作固定推荐名单。

```text
沪深300完整成分            300
  -> 五年股息率市场预筛通过  45
  -> 结构化硬门槛通过        15
  -> 正式Top10               10
```

| 排名 | 证券代码 | 公司 | 综合得分 |
|---:|---|---|---:|
| 1 | 601318.SH | 中国平安 | 82.62 |
| 2 | 600900.SH | 长江电力 | 74.58 |
| 3 | 600036.SH | 招商银行 | 72.83 |
| 4 | 601077.SH | 渝农商行 | 70.45 |
| 5 | 600919.SH | 江苏银行 | 65.30 |
| 6 | 601229.SH | 上海银行 | 62.35 |
| 7 | 601398.SH | 工商银行 | 57.42 |
| 8 | 600941.SH | 中国移动 | 57.08 |
| 9 | 601728.SH | 中国电信 | 54.79 |
| 10 | 601939.SH | 建设银行 | 53.52 |

这次运行还验证了以下完整性要求：

- 全量结果包含300个唯一成分，285个未入选项目均保留明确原因；
- 没有强制证据缺失的 `data_gap`，不会把缺失值当作0分参与排名；
- 20家市场预筛银行各覆盖最近3个审计年度，共60行银行专项指标，数据质量均为 `normal`；
- 除Top10外，同时生成完整候选CSV、中文报告、HTML看板，以及市场、公司资料、结构化财务、银行指标和原始证据缓存；
- Top10仅包含通过全部硬门槛的公司，并按综合得分降序排列。

## Skill 调用

安装或建立发现软链接后，在 Codex 中调用：

```text
$ahongli
```

手工建立软链接：

```bash
ln -s "$(pwd)" "$HOME/.agents/skills/ahongli"
```

## 环境准备

需要 Python、`pandas`、`tushare`、`httpx` 和 Poppler 的 `pdftotext`。

设置 Tushare Token：

```bash
export TUSHARE_TOKEN=your_token_here
```

不要把真实 Token、Cookie 或 `.env` 文件提交到 Git。CNinfo 通常不需要 Cookie；如运行环境确实要求，可临时设置：

```bash
export CNINFO_JSESSIONID=your_session_value
export CNINFO_INSERT_COOKIE=your_cookie_value
```

## 正式运行

在仓库根目录执行：

```bash
python3 scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD \
  --refresh-constituents
```

正式门槛要求过去五年有效交易日中，至少80%的交易日TTM股息率不低于3%。银行年报和专项指标缓存缺失时，主脚本会自动下载、提取、解析并验证最近三个审计年度。

只有确认当日市场、公司资料、结构化财务和银行缓存完整时才使用：

```bash
python3 scripts/run_a_dividend_strategy.py \
  --run-date YYYYMMDD \
  --skip-fetch
```

## 门槛模拟

模拟不会覆盖正式3%结果：

```bash
python3 scripts/simulate_dividend_threshold.py \
  --run-date YYYYMMDD \
  --yield-threshold 2.5
```

## 单独准备银行指标

```bash
python3 scripts/prepare_bank_metrics.py \
  --output a_dividend_outputs/YYYYMMDD/market_data/bank_quality_metrics.csv \
  --as-of-date YYYYMMDD \
  --bank-codes 600036.SH 601398.SH
```

## 输出

正式结果写入：

```text
a_dividend_outputs/YYYYMMDD/
```

主要文件：

- `hs300-dividend-candidates-YYYYMMDD.csv`：完整300家公司；
- `hs300-dividend-top10-YYYYMMDD.csv`：通过全部硬门槛的Top10；
- `hs300-dividend-report-YYYYMMDD.md`：中文报告；
- `hs300-dividend-dashboard-YYYYMMDD.html`：HTML看板；
- `market_data/bank_quality_metrics.csv`：银行三年专项指标与证据。

模拟结果写入独立的 `YYYYMMDD_sim_yield_*` 目录。

## 测试与校验

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile scripts/*.py
python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .
```

提交前建议再次运行敏感信息扫描，确认不存在真实凭据、本机绝对路径或私钥文件。
