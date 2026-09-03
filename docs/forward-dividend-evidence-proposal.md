# AHongli 前瞻分红证据与股息估值表改造提案

## 1. 提案状态

- 状态：已确认，第一版已实现
- 目标 Skill：`ahongli`
- 初稿日期：2026-09-01
- 修订日期：2026-09-02
- 改造类型：在正式选股完成后新增前瞻分红取证、预测和展示阶段
- 本文档用途：定义实现范围、证据合同、计算口径、输出格式、测试矩阵和验收标准；本文档本身不修改现有策略代码、因子、门槛、排名或输出。

本次修订确认以下实施边界：

- 第一版以前瞻DPS证据闭环和可复算目标股息率区间为核心，不承担完整估值系统和任意主观盈利调整；
- 第一版作为独立命令读取已落盘的正式Top10，前瞻失败不得阻止正式选股产物生成；
- 不使用历史P25—P75作为投入门槛；目标股息率必须由要求总回报率减可持续分红增长率推导；
- 证据完整性、预测方法和预测不确定性分开表达；
- 公告与财报下载能力抽取为共享模块，银行和前瞻分析共同复用；
- HTML和Markdown仅使用中性事实标签，不使用“持有”“击球”等交易暗示文案。
- 本策略及前瞻产物只面向A股标的：行情、DPS和未来12个月权益统一使用A股人民币口径。

## 2. 背景

当前 `ahongli` 已经完成以下正式流水线：

```text
沪深300完整成分
→ 五年股息率市场预筛
→ 结构化盈利、分红、现金流、审计、主营范围和银行专项硬门槛
→ 银行、其他金融、非金融分别评分
→ 正式 Top10
→ 全量 CSV、Top10 CSV、Markdown 和 HTML
```

现有策略擅长回答：

- 哪些公司具有长期股息率持续性；
- 哪些公司通过盈利、现金流、审计和分红安全门槛；
- 哪些公司在统一、可审计的质量红利模型中得分较高。

但正式 Top10 产生后，当前输出还不能完整回答：

1. 这家公司下一预测财年预计每股分红多少；
2. 预期分红来自已公告方案、正式政策还是分析推导；
3. 预期DPS的利润、派息率和股本假设分别是什么；
4. 当前价格对应的预期股息率是多少；
5. 承担该公司风险所要求的目标股息率区间是多少；
6. 预期DPS对应的目标价格区间是多少；
7. 每个预测数字能否追溯到原始财报、正式公告、页码和公式。

本提案建议在正式 Top10 之后新增一个独立的“前瞻分红证据与股息估值”阶段。

## 3. 改造目标

1. 保持现有300只完整选股、硬门槛、评分和正式Top10排名不变。
2. 仅对正式Top10构建原始财报和正式公告证据包，控制取证成本。
3. 将预期DPS作为一等派生指标，明确事实、假设、公式、情景、证据完整性和预测不确定性。
4. 同时保留原始综合得分和正式排名，不让预测分红改写选股分数。
5. 计算预期股息率、目标股息率区间、目标价格区间和当前位置。
6. 生成类似投资者分红观察表的简洁主表，同时提供逐家公司可追溯详情。
7. 任何进入主表的预期DPS必须能从原始证据完整复算。

## 4. 非目标

- 第一版不把预期DPS、预期股息率或价格位置加入现有选股评分。
- 第一版不改变3%正式市场预筛阈值、硬门槛、权重或Top10数量。
- 不读取或复用主观的 `financial-report-reader` 最终分析报告作为预测输入。
- 不要求为全部300家公司下载和解析完整财报；只对正式Top10执行深度取证。
- 不把Tushare结构化字段直接当作最终财报证据。
- 不将特别股息默认当作可持续常规DPS。
- 不输出无证据支撑的精确预测值。
- 不把最终表格表述为投资建议或交易指令。
- 第一版不允许未结构化、不可重复执行的人工正常化调整直接进入正式前瞻CSV。
- 目标价格区间仅由预期DPS和目标股息率机械换算，不表述为内在价值。
- 第一版不要求所有公司类型都能预测；模型不支持时明确输出 `unsupported`，不能临时套用不适用的通用公式。
- 不把H股、港元股息或H股除息日期混入A股DPS和股息率。

## 5. 核心原则

### 5.1 选股与预测解耦

正式Top10必须先由现有策略独立产生：

```text
formal_top_candidates(rows)
```

前瞻分红阶段只能读取该结果，不能修改：

- `rank`
- `dividend_score_total`
- `selected`
- `selection_status`
- `selected_reason`
- 任何硬门槛结果或因子贡献。

若某只Top10股票无法形成可靠预期DPS，它仍保留原排名和原分数。前瞻分红必须使用独立状态：

```text
announced      已公告完整常规DPS
modelled       已按受支持模型完成推导
data_gap       模型受支持，但强制证据缺失或冲突
unsupported    当前版本没有适用模型
failed         执行或解析异常
```

`data_gap`、`unsupported`和`failed`均不得显示伪造DPS；主表分别显示“证据不足”“当前模型不支持”或“前瞻分析失败”。

### 5.2 原始证据优先

最终证据优先级：

1. 交易所、巨潮、HKEX等正式分红实施公告或利润分配公告；
2. 公司正式年度报告、半年度报告、季度报告；
3. 股东会决议、董事会决议及正式分红回报规划；
4. 公司投资者关系网站正式材料；
5. Tushare等结构化数据库，仅用于初算、定位和交叉核对；
6. 行情终端字段，仅用于价格快照或口径明确的市场字段。

Tushare与原始文件不一致时：

- 原始财报和正式公告优先；
- 保留差异记录；
- 不得静默选用更方便或更有利的数值。

### 5.3 截止日期一致

所有报告、公告、行情和结构化数据必须满足：

```text
source_available_at <= cutoff_at
quote_at <= cutoff_at
```

日期级正式运行默认：

```text
cutoff_at = run_date 23:59:59 Asia/Shanghai
```

如果数据源只提供日期而不提供时间，必须记录 `available_time_quality=date_only`；需要盘中或收盘前历史回放时，日期级来源不能伪装成精确时间证据。行情使用不晚于截止时间的最近交易快照。

不得使用截止时间之后发布的信息回填历史结果。每个证据包必须记录：

- `run_date`
- `source_announcement_date`
- `source_available_date`
- `source_available_at`
- `available_time_quality`
- `quote_date`
- `quote_at`
- `cutoff_at`
- `forecast_as_of_date`

### 5.4 缺失不是零

无法取得预测所需的原始证据时：

```text
forecast_status = data_gap
forecast_dps = 空
forecast_yield = 空
forecast_reason = 具体缺失项
```

不能用0、上一年DPS或行情源股息率悄然顶替。

## 6. 新流水线与故障隔离

第一版不直接把阶段5至8嵌入现有正式runner，而是分成两个独立事务：

```text
事务A：正式选股
阶段1  刷新沪深300和公司资料
阶段2  五年股息率市场预筛
阶段3  结构化财务硬门槛与分类型评分
阶段4  原子写出正式300只、Top10、Markdown和HTML

事务B：前瞻分红
阶段5  读取已落盘的正式Top10，只对Top10准备原始证据包
阶段6  逐家公司选择受支持预测模型并计算DPS情景
阶段7  计算预期股息率、目标股息率区间和目标价格区间
阶段8  原子写出前瞻CSV、详情页、Markdown、HTML和运行状态
```

第一版命令建议为：

```bash
python3 scripts/run_forward_dividend_analysis.py \
  --run-date YYYYMMDD \
  --top10 a_dividend_outputs/YYYYMMDD/hs300-dividend-top10-YYYYMMDD.csv
```

前瞻功能稳定后，主runner可以增加显式 `--with-forward-dividend` 选项，但内部仍必须先提交事务A的正式产物，再执行事务B。阶段5至8失败时，不得回滚、覆盖或删除阶段1至4产物。

运行结果必须明确区分：

- 选股成功、前瞻分析成功；
- 选股成功、部分公司前瞻分析 `data_gap`；
- 选股成功、部分公司当前模型 `unsupported`；
- 选股成功、前瞻分析阶段整体失败。

`forward-dividend-run-status.json`必须记录事务B状态、开始和结束时间、输入Top10文件及SHA-256、成功/缺口/不支持/失败数量、错误摘要和输出文件列表。

## 7. 两类预期分红口径

为避免“预测财年分红”和“当前买入者未来现金权益”混淆，内部必须同时保留两个口径。

### 7.1 预测财年常规DPS

```text
forecast_fy_regular_dps
= 指定 forecast_fiscal_year 归属的全年常规税前每股现金分红
```

这是简洁主表中的“预期分红”，用于公司间横向比较和股息估值。

规则：

- 明确标注 `forecast_fiscal_year`；
- `instrument_ts_code`必须是正式Top10中的A股 `.SH` / `.SZ` 代码，`share_class=A`、`quote_currency=CNY`、`dividend_currency=CNY`；
- 默认选择运行日尚未完整公告全年常规DPS的最近一个公司财年；若该财年完整DPS已经公告，则状态为 `announced`，不是模型预测；
- 公司财年非自然年时，按公司正式财年截止日识别，不强制套用12月31日；
- 中期和末期常规股息合计；
- 特别股息默认排除；
- 已公告和分析推导部分分别保存；
- 不以实际派息自然年替代利润归属财年。

### 7.2 未来12个月当前买入者可获得DPS

```text
forward_12m_eligible_dps
= 行情快照日起未来12个月、当前买入者仍有资格取得的预计常规税前DPS
```

规则：

- 已经除息的历史分红不得计入；
- 已公告但尚未除息的股息可以计入；
- 尚未公告部分必须标记为预测；
- 预测除息日或支付日不确定时，必须保存日期假设及不确定性；
- 第一版仅在公司详情页和结构化CSV中保留该字段，不进入简洁主表；
- 该字段用于投资者现金回报补充，不替代主表的预测财年DPS。

## 8. Top10原始证据包

建议每家公司建立独立证据目录：

```text
a_dividend_outputs/{run_date}/market_data/forward_dividend_evidence/
└── {ts_code}/
    ├── manifest.json
    ├── reports/
    │   ├── {announcement_id}-annual.pdf
    │   ├── {announcement_id}-interim.pdf
    │   └── {announcement_id}-prior-comparable-period.pdf
    ├── announcements/
    │   ├── {announcement_id}-dividend-policy.pdf
    │   ├── {announcement_id}-interim-dividend.pdf
    │   ├── {announcement_id}-final-dividend.pdf
    │   └── {announcement_id}-implementation.pdf
    ├── extracted/
    │   ├── latest-annual.md
    │   ├── latest-interim.md
    │   ├── pages.json
    │   └── announcement-text.md
    ├── forecast-evidence.json
    └── forecast-detail.md
```

`manifest.json`至少记录：

```text
ts_code
company_name
run_date
source_type
source_title
source_file
source_url
source_final_url
announcement_id
announcement_date
available_date
available_at
available_time_quality
retrieved_at
sha256
extraction_backend
page_refs
supersedes_source_id
superseded_by_source_id
```

文件名必须包含稳定公告ID，不能用 `latest-annual.pdf` 等名称覆盖旧证据。`latest-*`只能作为manifest中的逻辑角色，不能作为原始证据身份。

### 8.1 证据数据层

前瞻阶段必须分离五层数据，避免在一个宽JSON中混合来源、事实和判断：

```text
source_document   原始PDF、URL、日期、哈希和修订关系
evidence_span     页码、位置、原文片段和提取器
normalized_fact   规范化数值、单位、期间、主体和股份类别
dividend_event    同一经济分红事件及其多个证据状态
forecast_result   模型版本、输入事实、假设、公式和输出情景
```

每层使用稳定ID引用上一层。预测结果只引用规范化事实ID，不直接复制无法追踪来源的裸数值。

## 9. 必须提取的事实字段

### 9.1 盈利与股本

```text
latest_fy_net_profit_parent
prior_same_period_net_profit_parent
latest_interim_net_profit_parent
latest_fy_adjusted_net_profit
latest_interim_adjusted_net_profit
normalized_or_ttm_profit
current_total_shares
forecast_total_shares
share_change_assumption
reporting_entity
instrument_ts_code
share_class
distribution_share_class
forecast_share_denominator_scope
quote_currency
dividend_currency
fx_rate
fx_rate_date
fx_source
```

### 9.2 历史分红

```text
regular_dps_2023
regular_dps_2024
regular_dps_2025
special_dps_2023
special_dps_2024
special_dps_2025
payout_ratio_2023
payout_ratio_2024
payout_ratio_2025
interim_to_full_year_ratio_history
```

### 9.3 当前预测证据

```text
announced_interim_dps
announced_final_dps
announced_special_dps
official_payout_floor
official_payout_target
official_dps_floor
policy_valid_from
policy_valid_to
policy_conditions
```

每个事实字段必须同时保存：

```text
value
unit
period
source_file
source_page_or_location
source_url
announcement_id
announcement_date
raw_evidence
source_document_id
evidence_span_id
reporting_entity
share_class
currency
normalization_rule
```

所有数值必须使用明确单位。利润、股本、每股金额和百分比不得依赖字段名猜测数量级；不同股份类别、不同币种或不同合并口径不得静默合并。

## 10. 分红事件处理

预案、股东会决议和实施公告通常是一笔分红的不同证据状态，不得重复累计。

建议分成：

```text
dividend_event
dividend_evidence
```

事件字段至少包括：

```text
event_id
fiscal_period
dividend_type
regular_or_special
cash_dividend_per_share_pre_tax
currency
status
record_date
ex_dividend_date
payment_date
distribution_base_shares
total_cash_dividend
```

公告ID只属于证据记录，不作为同一经济事件的唯一去重键。

前瞻事件只接受A股税前现金分红。混合A/H公告必须优先提取明确标注的A股人民币DPS；只有H股或港元数据时，该事件对A股口径为 `data_gap`。使用集团归母利润和派息率预测DPS时，分母可以是适用于利润分配的全部普通股，但必须以 `forecast_share_denominator_scope=all_ordinary_shares` 明示，不能误标成A股流通股数。

同一事件建议使用以下业务键辅助去重，并保留人工不可判定状态：

```text
issuer + fiscal_period + dividend_type + share_class
+ cash_dividend_per_share_pre_tax + record_date_or_distribution_base
```

预案、董事会决议、股东会决议和实施公告是同一事件的不同证据状态。修订公告必须通过 `supersedes_source_id` 覆盖旧事实，旧证据仍保留但不得继续参与当前计算。

## 11. 盈利基准

### 11.1 默认TTM公式

对累计披露的中国会计准则报表：

```text
TTM归母净利润
= 上一完整财年归母净利润
- 上年同期累计归母净利润
+ 本年同期累计归母净利润
```

不得简单把H1乘以2。

### 11.2 正常化调整

出现下列情况时，必须评估是否用正常化利润替代GAAP TTM：

- 同一控制下合并或追溯调整；
- 大额资产处置；
- 公允价值变动或投资收益异常；
- 一次性减值或转回；
- 资源品价格处于明显周期极值；
- 保险投资收益导致GAAP利润大幅波动；
- 银行信用成本或拨备一次性释放。

任何正常化调整必须列明：

```text
reported_profit
adjustment_item
adjustment_amount
normalized_profit
adjustment_source
adjustment_reason
```

第一版的正式前瞻结果只接受以下三类输入：

1. 原始报告利润；
2. 可由累计报表机械复算的TTM利润；
3. 公司正式披露、定义明确且能定位原文的营运利润或同类指标。

资产处置、公允价值、周期极值、减值、拨备释放等自由判断不得直接写入正式结果。后续若引入正常化调整，必须使用版本化 `adjustment_record`：

```text
adjustment_id
adjustment_type
amount
direction
source_fact_id
rule_version
reason
```

不满足受支持调整规则时，使用低/基准/高情景表达不确定性，或将结果标记为 `unsupported` / `data_gap`，不得依靠自由文本手工修正后继续给出精确DPS。

## 12. 派息率选择

模型候选优先级：

1. 已公告完整DPS时不再反推派息率；
2. 正式有效的公司派息率目标；正式下限只作为低情景；
3. 已公告中期DPS与公司全年政策的组合；
4. 最近三年常规派息率中位数；
5. 最近DPS延续，仅限分红稳定且盈利、现金流能够覆盖；
6. 证据不足时 `data_gap`。

三年派息率超过100%不自动视为可持续。除非存在正式政策和现金来源证据，预测模型不得默认使用超过100%的派息率。

第一版默认启用第1至3类方法。第4类只能在已经实现并测试的分类型模型中使用，例如带资本约束的银行模型；第5类不作为通用后备方法启用。公司没有适用模型时输出 `unsupported`，不能因为历史DPS稳定就自动延续。对银行，若正式政策只给出下限而最近三年实际派息率持续高于下限，则下限进入低情景，三年实际派息率中位数进入基准情景，最近三年较高值进入高情景。

## 13. 分类型预测模型

预测模型采用能力注册方式，不按股票代码在主流程中硬编码：

```text
model_id
model_version
supported_company_type
required_fact_types
optional_fact_types
supports(company, facts)
forecast(facts, assumptions)
```

只有 `supports(...)` 返回通过且强制事实完整时才能执行。公司属于已知类型但当前版本没有合适模型时，状态为 `unsupported`，不能退回到不适用的通用公式。

### 13.1 普通稳定公司

```text
forecast_dps
= normalized_profit
× sustainable_payout_ratio
÷ forecast_total_shares
```

使用已公告中期DPS作为下限交叉验证，并检查经营现金流与自由现金流覆盖。

第一版普通稳定公司只有在存在正式派息率政策、DPS下限或已公告中期股息与全年政策组合时启用该模型；仅有历史派息率时不自动执行。

### 13.2 银行

核心证据：

- 预测归母净利润；
- 正式派息率或三年派息率；
- 核心一级资本充足率；
- 总资本充足率；
- 不良率、拨备覆盖和贷款拨备率；
- 资本补充计划。

银行预测不得只因历史派息率稳定而忽略资本约束。

银行派息证据必须按完整年度绑定。中期分红不能被当作完整年度派息率；“全年DPS”已经包含中期DPS时，不得再与中期DPS相加。年度报告脚注中的历史重述比例不得绑定到当前报告年度。

银行模型应复用现有三年银行专项缓存中的资本、不良和拨备事实，但前瞻预测使用的关键数据仍需引用原始年报证据ID。若资本补充计划或监管约束无法取得，不得把历史派息率机械延续为低不确定性预测。

### 13.3 保险及其他金融

保险公司不得机械使用GAAP净利润预测分红。优先使用：

- 归母营运利润；
- 已公告中期股息及同比变化；
- 历史DPS稳定性；
- 内含价值、新业务价值和偿付能力；
- 管理层正式股东回报表述。

第一版可以只实现一个能力受限的 `insurance_operating_profit_policy_v1` 模型。中国平安仅在强制事实完整时使用该模型；其他保险公司若不满足相同事实合同，标记 `unsupported`，而不是按股票代码默认复制平安逻辑。

### 13.4 公用事业

除利润和派息率外，检查：

- 发电量、电价和燃料成本；
- 来水、利用小时和装机变化；
- 财务费用；
- 资本开支；
- 少数股东股利；
- 净债务变化；
- 正式DPS或派息率下限。

### 13.5 强周期资源公司

不得仅使用单点TTM利润。至少输出：

```text
downside_normalized_profit
base_normalized_profit
upside_normalized_profit
```

并以产量、商品价格、单位成本、汇率和派息政策为证据。主表可展示基准DPS，但预测不确定性不得为 `low`。

第一版不采用三年或五年历史利润中位数作为正式强周期预测。只有公司专属驱动模型能够由产量、价格、成本、汇率和派息政策复算时才输出结果；否则标记 `unsupported`。

## 14. DPS情景、证据完整性与不确定性

每家公司至少输出：

```text
forecast_dps_low
forecast_dps_base
forecast_dps_high
```

情景变化必须来自可解释的利润、派息率或股本参数，不允许直接对DPS任意加减百分比。

不得把来源质量、预测方法和模型不确定性压缩成单一A—E等级。必须分别输出：

### 14.1 证据完整性

```text
complete   强制事实均有原始来源、位置和可复算值
partial    非关键事实缺失，但不影响基准计算
conflict   关键来源之间存在未解决冲突
missing    强制事实缺失
```

### 14.2 预测方法

```text
announced             已公告完整常规DPS
policy_derived        正式政策＋可复算盈利基准
policy_and_history    正式下限用于低情景＋完整年度实际派息率用于基准/高情景
historical_payout     报告利润＋历史常规派息率
company_driver_model  公司专属驱动模型
```

### 14.3 预测不确定性

```text
low            已公告完整方案，或剩余变量不会实质改变DPS
medium         盈利、派息率或股本仍存在有限情景差异
high           周期、资本约束或关键经营变量造成宽区间
not_estimable  无法形成可靠情景
```

证据完整不等于预测不确定性低。例如，强周期公司可以拥有 `complete` 证据，但模型不确定性仍为 `high`。主表分别展示“预测依据”和“不确定性”；详情页展示完整三维状态。

## 15. 当前股息率与预期股息率

```text
expected_dividend_yield
= forecast_dps_base / quote_price
```

规则：

- 使用同一行情快照的股价；
- 标注价格日期；
- 行情必须来自正式Top10中的A股代码并使用人民币；
- 当前行情源股息率原样保留并标明口径；
- 行情源股息率口径不明时，不得用它反推预期DPS；
- 预期股息率必须由本阶段的预期DPS自行计算。

## 16. 目标股息率与目标价格区间

历史P25—P75反映过去市场定价，不能直接回答投资者为承担当前公司风险应要求多少收益率，因此从正式输出中移除。目标股息率使用以下可复算模型：

```text
要求总回报率区间
= 无风险利率 + 行业及公司风险溢价区间

可持续分红增长率
= clamp(min(五年可比DPS CAGR, ROE × (1 - 最新派息率)), 行业增长下限, 行业增长上限)

目标股息率区间
= 要求总回报率区间 - 可持续分红增长率
```

无风险利率采用运行日之前最新可取得的十年期中国国债收益率，并保存数值、日期和来源。行业风险溢价及增长上下限属于版本化策略参数，不得根据当前股价反推，也不得逐公司手工修改以迎合结果。

第一版行业参数：

| 类型 | 要求总回报率参考 | 可持续增长限幅 |
|---|---:|---:|
| 稳定水电 | 8%–9% | 2%–4% |
| 银行 | 8.5%–10% | 2%–3% |
| 保险 | 8%–10% | 2%–3% |
| 电信运营 | 8%–10% | 2%–3% |
| 其他 | 9%–11% | 1%–3% |

银行承担信用、利率、资产质量和资本约束风险，目标股息率下限额外增加0.5个百分点安全边际，上限保持不变。实现上将银行风险溢价下限从6.3%提高至6.8%；因此原 `5%–7%` 调整为 `5.5%–7%`，原 `6%–8%` 调整为 `6.5%–8%`，非银行区间不变。

目标价格区间只做机械换算：

```text
target_price_low = forecast_dps_base / target_yield_high
target_price_high = forecast_dps_base / target_yield_low
```

目标区间以0.5个百分点为展示精度。若预期DPS缺失，仍可保留目标股息率，但目标价格和位置保持为空；区间不是内在价值，也不构成交易建议。

## 17. 当前位置标签

第一版只使用中性标签，不构成交易建议：

```text
expected_yield > target_yield_high
→ 高于目标，核查风险

target_yield_low <= expected_yield <= target_yield_high
→ 进入目标区间

expected_yield < target_yield_low
→ 未达目标区间

证据不足
→ 暂不判断
```

用户可见标签统一为：

```text
高于目标，核查风险
进入目标区间
未达目标区间
暂不判断
```

不得使用“击球区间”“持有收息”“持有观望”等可能被理解为交易建议的标签。

## 18. 主表输出合同

主表必须同时保留原有正式排名和综合得分。

建议字段顺序：

| 排名 | 公司 | 得分 | 股价 | 预期分红 | 预期股息率 | 当前位置 | 目标股息率区间 | 目标价格区间 |
|---:|---|---:|---:|---:|---:|---|---:|---:|
| 1 | 示例公用事业 | 80.00 | 25.00 | 0.90 | 3.60% | 未达目标区间 | 4.00%–5.00% | 18.00–22.50 |
| 2 | 示例银行 | 75.00 | 10.00 | 0.45 | 4.50% | 未达目标区间 | 5.50%–7.00% | 6.43–8.18 |
| 3 | 示例缺口公司 | 70.00 | 18.00 | — | — | 暂不判断 | 6.00%–8.00% | — |

以上数值仅用于展示输出合同，不代表任何真实公司预测或投资结论。

规则：

- `排名`和`综合得分`直接来自正式Top10，不重新计算；
- 主表仍按正式综合得分降序，不按预期股息率排序；
- 预期分红数据不得改变排名；
- 主表简洁展示，逐家公司详情保存完整证据；
- 第一版不在主表展示 `forward_12m_eligible_dps`，该字段保留在CSV和详情页；
- 主表参考投资者分红观察表，只保留排名、公司、得分、股价、预期分红、预期股息率、当前位置、目标股息率区间和目标价格区间；
- 预期股息率必须由基准DPS与同日股价复算；
- 目标价格、DPS三情景、预测方法、证据完整性和不确定性保留在CSV与详情页；
- 公司名应链接到或明确指向对应 `forecast-detail.md`；
- `data_gap`、`unsupported`和`failed`公司仍保留排名和分数，预测字段保持空值并显示对应状态原因。

## 19. 新增输出产物

保留现有所有正式输出，额外新增：

```text
a_dividend_outputs/{run_date}/
├── hs300-dividend-forward-top10-{run_date}.csv
├── hs300-dividend-forward-report-{run_date}.md
├── hs300-dividend-forward-dashboard-{run_date}.html
└── market_data/
    ├── forward-dividend-run-status.json
    ├── forward-dividend-sources.csv
    ├── forward-dividend-facts.csv
    ├── forward-dividend-events.csv
    ├── forward-dividend-results.csv
    └── forward_dividend_evidence/{ts_code}/...
```

现有文件保持兼容：

```text
hs300-dividend-candidates-{run_date}.csv
hs300-dividend-top10-{run_date}.csv
hs300-dividend-report-{run_date}.md
hs300-dividend-dashboard-{run_date}.html
```

不建议把前瞻字段直接塞入完整300只候选CSV。前瞻取证只覆盖Top10，独立文件更能区分选股证据与预测证据。

## 20. 逐家公司详情页

每个 `forecast-detail.md` 至少包括：

```text
# 公司名称 前瞻分红证据

## 预测结论
## 使用的财报与公告
## 原始盈利与股本数据
## 历史常规分红与特别股息
## 公司正式分红政策
## 正常化或TTM利润计算
## 派息率选择
## 预期DPS三情景
## 预期股息率、目标股息率与目标价格区间
## Tushare交叉核对
## 风险、缺失证据与失效条件
```

详情页不是17节财报分析报告，只聚焦前瞻分红所需事实和计算。

## 21. 建议代码结构

避免继续扩大 `run_a_dividend_strategy.py` 单文件，建议新增：

```text
scripts/
├── cninfo_client.py
├── announcement_resolver.py
├── document_cache.py
├── forward_dividend_models.py
├── prepare_forward_dividend_evidence.py
├── parse_forward_dividend_evidence.py
├── forecast_selected_dividends.py
├── render_forward_dividend_outputs.py
└── run_forward_dividend_analysis.py
```

职责：

- `cninfo_client.py`：通用CNinfo查询和下载，不包含银行或前瞻业务判断；
- `announcement_resolver.py`：实体解析、公告分类、截止日期、修订关系和经济事件候选；
- `document_cache.py`：公告ID文件命名、SHA-256、缓存命中、原子写入和损坏检测；
- `forward_dividend_models.py`：纯计算函数、分类型模型、情景和状态；
- `prepare_forward_dividend_evidence.py`：下载、缓存、哈希和截止日期控制；
- `parse_forward_dividend_evidence.py`：从原始报告和公告提取事实字段及页码证据；
- `forecast_selected_dividends.py`：只接收正式Top10，组织预测并写结构化结果；
- `render_forward_dividend_outputs.py`：生成CSV、Markdown、HTML和详情页；
- `run_forward_dividend_analysis.py`：独立事务入口、运行状态和错误隔离。

现有 `download_bank_reports.py` 和 `prepare_bank_metrics.py` 保持兼容，但底层逐步改为调用共享CNinfo模块。前瞻实现不得依赖当前仅覆盖银行的紧凑 `assets/stocks.json`；实体解析必须覆盖正式Top10中的非银行公司，并将无法解析的代码标记为 `data_gap`。

第一版现有主runner不直接调用前瞻模块。稳定后的可选集成只负责：

```text
top = formal_top_candidates(rows)
write_formal_outputs_atomically(rows, top, ...)
if with_forward_dividend:
    run_forward_dividend_transaction(top_file, run_date, market_dir)
```

## 22. 建议结构化字段

前瞻Top10 CSV至少包含：

```text
rank
ts_code
name
dividend_score_total
quote_price
quote_date
instrument_ts_code
share_class
quote_currency
dividend_currency
forecast_share_denominator_scope
fx_rate
fx_rate_date
fx_source
source_dividend_yield
source_dividend_yield_definition
forecast_fiscal_year
forecast_fy_regular_dps_low
forecast_fy_regular_dps_base
forecast_fy_regular_dps_high
forward_12m_eligible_dps
expected_dividend_yield
target_yield_low
target_yield_high
target_price_low
target_price_high
target_status
target_display_label
target_yield_model_id
target_yield_model_version
target_yield_category
risk_free_rate
risk_free_rate_date
risk_free_rate_source
equity_and_company_risk_spread_low
equity_and_company_risk_spread_high
required_return_low
required_return_high
sustainable_dividend_growth
target_yield_basis
forecast_method
model_id
model_version
evidence_completeness
forecast_uncertainty
forecast_status
forecast_reason
forecast_profit_low
forecast_profit
forecast_profit_high
forecast_payout_ratio_low
forecast_payout_ratio
forecast_payout_ratio_high
forecast_total_shares
announced_dividend_floor
special_dividend_excluded
forecast_input_fact_ids
forecast_input_event_ids
forecast_selection_decisions
evidence_detail_path
```

CSV空值必须保持为空，不得在数值列写入“未能可靠估计”等文本。用户可见说明由状态和原因字段负责。

## 23. 测试矩阵

实施必须遵循TDD，先写失败测试，再修改实现。

### 23.1 选股隔离

1. 前瞻预测只接收正式Top10。
2. 非selected股票不触发财报下载或预测。
3. 前瞻阶段不得修改排名、综合得分、硬门槛和因子贡献。
4. 前瞻阶段部分失败时，原有300行CSV和Top10仍正常生成。
5. 前瞻入口只读取已经落盘的Top10，输入文件SHA-256写入运行状态。
6. 前瞻进程异常退出时，正式选股文件内容和mtime不发生变化。
7. 前瞻输出使用临时文件原子替换，不留下被误认为成功的半成品。

### 23.2 分红事件

1. 预案、决议和实施公告不得重复累计。
2. 中期和末期按归属财年正确合计。
3. 特别股息默认排除并单列。
4. 已除息股息不得计入未来12个月当前买入者口径。
5. 股本变化按登记日或公告分红基准股本还原。
6. 外币和不同股份类别不得混用。
7. 混合A/H公告只提取A股人民币DPS，H股单独公告对本策略为 `data_gap`。
8. 修订公告覆盖旧事实，但旧来源和修订链仍可审计。
9. 相同经济事件的预案、决议和实施公告共享同一 `event_id`。

### 23.3 盈利与派息率

1. TTM使用完整公式，不得把H1乘2。
2. 同一控制合并和追溯调整使用最新可比口径。
3. 正式派息率目标可作为基准；正式派息率下限只能作为低情景，不得覆盖持续高于下限的实际派息率基准。
4. 超过100%的历史派息率不得自动延续。
5. 保险模型不得机械使用GAAP利润。
6. 强周期模型必须输出三情景且预测不确定性不能为 `low`。
7. 当前模型不支持的公司输出 `unsupported`，不得退回通用公式。
8. 自由文本人工调整不能进入正式预测输入。
9. 年度派息率只使用完整年度事实，并优先采用明确年度利润分配方案；历史脚注不得冒充当前年度。
10. 全年DPS已包含中期DPS时不得重复累计。
11. 利润句中的派息百分比和分红总额不得被解析成上年同期利润。

### 23.4 目标股息率

1. 要求总回报率可由无风险利率和版本化风险溢价复算。
2. 可持续增长率可由五年DPS CAGR、ROE和派息率复算并通过行业上下限约束。
3. 目标股息率不得由当前股价或当前股息率反推。
4. 目标价格上下限可由基准DPS和目标股息率上下限复算。
5. 无效无风险利率必须拒绝并记录失败状态。
6. CSV和主表不得再出现历史P25、P50、P75字段。

### 23.5 代表性回归案例

至少覆盖：

- 长江电力：2026—2030年70%派息政策，中期和末期合计，TTM利润公式；
- 国电电力：2025—2027年60%派息率和0.22元DPS下限；
- 中国平安：使用营运利润；完整年度派息率采用同年度多处披露的共识值，忽略历史重述脚注和中期报告重复历史表；
- 中国移动：已公告中期股息与全年派息率交叉验证；
- 招商银行：30%政策下限作为低情景、最近三年实际派息率作为基准/高情景，并检查资本约束；
- 中国电信：75%派息政策有效期、利润句中的百分比/分红总额不得污染同比利润；
- 建设银行：全年DPS已经包含中期DPS，不得重复累计；
- 利润骤降公司：不得照搬上一年度DPS；
- 强周期资源公司：公司专属驱动模型和高不确定性；
- 特别股息公司：常规与特别股息分离。

### 23.6 输出合同

1. 主表包含原排名和原综合得分。
2. 主表仍按综合得分降序。
3. CSV中的预期股息率可由DPS和股价复算。
4. 目标价格上下限可由基准DPS和目标股息率上下限复算。
5. HTML主表与CSV事实一致。
6. 每行能定位到详情页和原始证据。
7. `data_gap`、`unsupported`和`failed`不显示伪造数值。
8. CSV数值列只包含数值或空值，状态说明不混入数值列。
9. 证据完整性与预测不确定性保留在CSV和详情页，不挤入简洁主表。
10. 主表不出现“持有”“买入”“击球”等交易暗示标签。
11. 独立runner结束后在stdout打印与前瞻CSV一致的简洁A股Top10表；状态JSON文件仍是机器读取的运行状态来源。

## 24. 验收标准

改造完成至少满足：

1. 正式300只候选CSV行数、Top10排名和分数与改造前一致。
2. 前瞻阶段只处理不超过10只正式入选股票。
3. Top10每家公司都有独立证据包，或明确 `data_gap` / `unsupported` / `failed` 原因。
4. 每个预期DPS都能从原始事实、假设和公式完整复算。
5. 原始事实均带来源文件、页码/位置、公告ID或URL和日期。
6. Tushare与财报原文存在差异时保留差异说明。
7. 主表只保留排名、公司、综合得分、股价、预期DPS、预期股息率、位置、目标股息率区间和目标价格区间。
8. 预期DPS不参与原选股分数。
9. 证据缺失、证据冲突和强周期公司不伪装成低不确定性精确预测。
10. 所有新增测试、现有全量测试、Python编译检查和Skill校验通过。
11. 前瞻阶段重复运行结果幂等；损坏缓存会被哈希校验发现并重建或标记失败。
12. 同一运行日改造前后的正式300只CSV和Top10关键字段逐项一致。

## 25. 实施阶段建议

### 第一阶段：纯计算与合同

- 定义五层证据数据结构、状态、模型能力和纯函数；
- 实现TTM、派息率、DPS情景和预期股息率；
- 冻结一份现有正式300只和Top10基线，用于隔离回归；
- 使用固定夹具完成代表性回归测试；
- 暂不接入网络下载。

### 第二阶段：Top10证据准备

- 从正式Top10生成下载清单；
- 抽取共享CNinfo客户端、实体解析器和文档缓存；
- 下载原始财报、分红公告和政策文件；
- 生成manifest、哈希、修订链、文本和页级证据；
- 实现截止时间控制、缓存复用、损坏检测和原子写入。

### 第三阶段：分类型预测

- 普通公司；
- 银行；
- 保险及其他金融；
- 公用事业；
- 电信运营；
- 强周期资源公司仅在公司专属驱动模型完成后支持。

每增加一种模型，先加入真实回归夹具和失败测试。

### 第四阶段：历史常规股息率重建

- 从经济分红事件生成当时可获得的常规TTM DPS时间段；
- 与同股份类别、同币种的历史价格连接；
- 计算目标股息率区间和目标价格区间；
- 对行业风险参数、增长限幅和无风险利率建立回归夹具。

### 第五阶段：展示与独立运行

- 新增前瞻Top10 CSV；
- 新增Markdown和HTML主表；
- 生成逐家公司详情页；
- 通过独立命令读取已落盘Top10；
- 用同一运行日验证正式300只、Top10排名和分数不变；
- 对全部Top10逐一做公式、来源和链接审计。

### 第六阶段：可选主runner集成

- 仅在独立运行稳定后增加 `--with-forward-dividend`；
- 正式选股事务必须先原子完成；
- 前瞻失败只更新独立运行状态，不改变正式选股退出结果和产物。

## 26. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 预测逻辑污染选股 | 独立事务读取已落盘Top10；测试正式文件内容和mtime不变 |
| Tushare重复分红事件 | 原始公告事件模型和证据状态去重 |
| 财报解析错误 | 保存原文、页码、raw evidence和人工复核位置 |
| 未来信息泄漏 | 所有来源执行时间截止；保存公告时间、可用时间和行情时间 |
| 单文件继续膨胀 | 抽取共享CNinfo、解析、模型、运行和渲染模块 |
| 特别股息抬高预测 | 常规与特别股息分开，默认排除特别股息 |
| 保险/周期股机械套公式 | 能力型模型、三情景和不确定性分级 |
| 目标股息率主观漂移 | 固定公式与版本化行业参数，不根据当前价格倒推或逐公司手调 |
| 股份类别或币种混用 | 显式保存股份类别、两个币种、汇率日期和来源 |
| 人工正常化不可复算 | 第一版禁止自由文本调整；后续只接受版本化调整规则 |
| 缓存损坏或半成品 | SHA-256校验、原子写入、运行状态和幂等重跑 |
| 表格简洁导致证据丢失 | 主表链接详情页，详情页保存完整证据链 |

## 27. 第一版已确认决策

| 决策 | 第一版结论 |
|---|---|
| 目标股息率区间 | 使用“要求总回报率减可持续分红增长率”，历史P25/P50/P75退出前瞻产物 |
| `forward_12m_eligible_dps` | 保留在CSV和详情页，不进入简洁主表 |
| 保险模型 | 优先使用 `insurance_operating_profit_policy_v1`；正式政策缺失但三年原始证据派息率可复算时，使用高不确定性的历史派息模型；其他不满足者为 `unsupported` |
| 强周期利润 | 不使用机械三年/五年中位数；需要公司专属驱动模型，否则为 `unsupported` |
| 公告下载 | 抽取共享CNinfo客户端、公告解析和文档缓存，银行与前瞻共同复用 |
| HTML位置标签 | 使用“未达目标区间”“进入目标区间”“高于目标，核查风险”和“暂不判断” |
| 运行方式 | 第一版独立读取正式Top10；稳定后才提供主runner显式集成 |
| 价格区间命名 | 使用“目标价格区间”，强调由预期DPS和目标股息率机械换算，不称内在价值 |
| 证券口径 | 全部使用A股代码、人民币股价和A股税前DPS；H股/港元信息不能混入 |

## 28. 预期收益

采用本提案后，`ahongli`将能够在不破坏现有正式质量红利排名的前提下，同时回答：

- 为什么这家公司进入Top10；
- 它的正式排名和综合得分是多少；
- 下一预测财年预计每股分红多少；
- 预测基于哪些财报、公告、政策和假设；
- 当前价格对应的预期股息率是多少；
- 承担公司风险所要求的目标股息率区间是多少；
- 当前预期股息率是否进入目标区间；
- 哪些结论证据充分，哪些仍有明显不确定性。

最终主表保持简洁，但任何数字都能下钻到原始证据、页码和完整公式，达到可复算、可审计、经得起推敲的标准。

## 29. 第一版实现与真实验证

第一版实现包含共享CNinfo客户端、公告解析、文档哈希缓存、页级PDF文本提取、规范化事实、分红经济事件、纯计算模型、独立runner、CSV/Markdown/HTML渲染和逐公司详情页。

2026-09-02使用正式 `20260901` Top10完成真实运行：

```text
正式Top10输入            10
modelled                 9
data_gap                 1
unsupported              0
failed                   0
```

2026-09-03复核并修正平安历史脚注、招行政策下限、电信利润/政策和建行全年DPS重复累计后，正式 `20260901` Top10全部10家公司均形成可复算预测，`data_gap=0`。主表保留正式得分并增加目标价格区间。目标区间不依赖历史分位数，也没有用H股股息、行情源股息率或上一年DPS静默填充。

真实运行确认正式Top10文件SHA-256和mtime在前瞻事务前后保持不变。前瞻阶段独立写出主CSV、Markdown、HTML、运行状态、source/fact/event/result审计CSV和每家公司详情页。
