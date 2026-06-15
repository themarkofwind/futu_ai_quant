# futu_ai_quant 开发指南

本文档说明重构后项目的**入口**、**启动方式**、**执行流程**，以及各模块职责与外部 API 调用关系。代码内各包/模块的 docstring 与本指南保持一致。

---

## 一、项目入口一览

| 入口方式 | 命令 / 模块 | 实际执行 | 用途 |
|----------|-------------|----------|------|
| 根目录脚本（推荐习惯） | `python main.py` | `futu_ai_quant.cli.analyze.main` | 持仓量化分析 + AI/规则决策 |
| 根目录脚本 | `python sim_trader.py` | `futu_ai_quant.cli.sim.main` | 按决策模拟撮合 / 绩效 |
| 包模块入口 | `python -m futu_ai_quant` | 同上 analyze | 与 `main.py` 等价 |
| pip 安装后命令 | `futu-analyze` | `cli.analyze.main` | 需 `pip install -e .` |
| pip 安装后命令 | `futu-sim` | `cli.sim.main` | 需 `pip install -e .` |
| pip 安装后命令 | `futu-backtest` | `cli.backtest.main` | 信号历史回测（规则引擎，不调 LLM） |
| 程序化调用 | `from futu_ai_quant.pipeline.cycle import run_analysis_cycle` | 单轮分析，不进入 CLI 循环 | 被 sim `--source main` 等复用 |
| 兼容导入 | `from main import run_analysis_cycle, ...` | 根目录 `main.py` 再导出 | 旧脚本兼容 |

**根目录薄封装：**

- `main.py` — 转发到 `cli/analyze.py`，并导出 `run_analysis_cycle`、`OpenHKTradeContext` 等符号供 `sim_trader` 历史写法使用。
- `sim_trader.py` — 仅转发到 `cli/sim.py`。

---

## 二、启动前置条件

1. **Python 3.12+**（`pandas-ta` 要求）
2. **Futu OpenD** 本地运行并已登录（默认 `127.0.0.1:11111`）
3. **`.env` 配置**（从 `.env.example` 复制）：
   - `DEEPSEEK_API_KEY` 或 `LLM_PROVIDER` + 对应 Key — 分析走 AI 时必填；可用 `--no-ai` 跳过
   - `FUTU_TRADE_UNLOCK_PWD` — 实盘查询持仓/成交时建议配置
4. **依赖安装**：
   ```bash
   pip install -r requirements.txt
   # 开发 / 测试
   pip install -r requirements-dev.txt
   ```

---

## 三、分析程序（main / futu-analyze）

### 3.1 常用命令

```bash
# 单次分析后退出（调试首选）
python main.py --once

# 不调用 LLM，仅用规则引擎（无需 API Key）
python main.py --once --no-ai

# 切换 LLM 提供商
LLM_PROVIDER=openai LLM_MODEL=gpt-4o-mini python main.py --once

# 常驻：盘中约 30 分钟、盘外约 4 小时一轮（见 market/session.py）
python main.py

# 等价
python -m futu_ai_quant --once
```

### 3.2 启动后执行流程

```
cli/analyze.main()
  ├─ load_dotenv()                    # 加载 .env
  ├─ create_llm_client()              # 按 LLM_PROVIDER 创建客户端（use_ai 时）
  ├─ OpenQuoteContext(host, port)     # Futu 行情上下文
  ├─ OpenHKTradeContext(...)          # Futu 港股交易上下文
  ├─ maybe_unlock_trade(trade_ctx)    # 可选交易解锁
  └─ 循环:
       run_analysis_cycle(...)         # 见 pipeline/cycle.py
       sleep(resolve_analysis_interval())
```

### 3.3 单轮分析 pipeline（`run_analysis_cycle`）

| 步骤 | 模块 | 主要方法 | 说明 |
|------|------|----------|------|
| 1 | `brokers/futu/positions` | `get_position_list` | 拉取港股实盘持仓 |
| 2 | `domain/positions` | `classify_positions` | 正股/期权分类、方向标注 |
| 3 | `brokers/futu/quotes` | `fetch_snapshot_map` | 批量现价快照 |
| 4 | `history/trades` | `load_ytd_trade_history` | 当年成交（磁盘/内存缓存 + 增量 API，失败回退旧缓存） |
| 5 | `analysis/stock` | `compute_stock_indicators` | 日K/周K指标、`technical_ensemble`、卖权扫描、交易计划 |
| 5a | `risk/position_limits` | `attach_portfolio_risk_limits` | 波动率 + 相关性动态限仓 |
| 5b | `analysis/stock` | `rebuild_stock_trade_plans` | 按调整后仓位上限重建 `stock_trade_plan` |
| 6 | `history/trades` | `attach_trade_history_to_stocks` | 挂载 YTD；正股/期权各最近 N 笔（默认各 5 笔） |
| 7 | `brokers/futu/options` | `fetch_option_metrics` | 持仓期权 IV / Greeks |
| 8 | `analysis/portfolio` | `attach_stock_option_context` | 正股挂载已有期权与建议卖权 |
| 9 | `analysis/portfolio` | `build_portfolio_payload` | 组装发给 LLM 的 JSON（含 `dynamic_risk`、`technical_ensemble`） |
| 10 | `decision/ai` 或 `decision/rules` | `call_llm_decision` / `build_rules_decision` | 生成 recommendations |
| 11 | `decision/validation` | `validate_decision_schema` | 整手、字段完整性校验 |
| 12 | `decision/storage` | `save_analysis_artifacts` | 输入写入 `data/payloads/`，输出写入 `data/decisions/` |

**输出：**

- 输入 review：`data/payloads/payload_YYYYMMDD_HHMMSS.json`、`latest_payload.json`
- 决策输出：`data/decisions/decision_YYYYMMDD_HHMMSS.json`、`latest.json`（含 `payload_path` 关联输入）

---

## 四、信号历史回测（futu-backtest）

在不调用 LLM 的前提下，用历史日 K 逐日回放波段规则，统计信号触发后的 5/10 日前瞻收益与胜率。

### 4.1 常用命令

```bash
# 默认 pl_ratio=-30%（中度亏损分层）
futu-backtest --code HK.09988

# 指定模拟盈亏分层
futu-backtest --code HK.00700 --pl-ratio -50

# JSON 输出
futu-backtest --code HK.09988 --json
```

### 4.2 执行流程

```
cli/backtest.main()
  ├─ OpenQuoteContext
  ├─ fetch_history_kline_cached()     # 拉取日 K
  └─ backtest/signals.run_signal_backtest()
       ├─ 逐日切片 K 线
       ├─ compute_indicators_from_frame（日K + 重采样周K）
       ├─ resolve_effective_swing_signal
       └─ 统计 BUY_SWING / SELL_SWING 的前瞻收益
```

**模块：** `futu_ai_quant/backtest/signals.py`

---

## 五、模拟交易（sim_trader / futu-sim）

### 5.1 常用命令

```bash
# 首次：镜像真实持仓初始化模拟账户
python sim_trader.py --init-mirror

# 按最新决策跑一轮
python sim_trader.py --source latest --once

# 先跑分析再模拟（一步完成）
python sim_trader.py --source main --once

# 查看累计绩效
python sim_trader.py --report

# 同步提交 Futu 模拟盘订单
python sim_trader.py --backend futu --source latest --once
```

### 5.2 启动后执行流程

```
cli/sim.main()
  ├─ --report → sim/io.print_report() 后直接退出
  ├─ --init-mirror / --init-cash → 初始化 data/sim/portfolio.json
  └─ 否则:
       PaperPortfolio.load()
       LocalSimEngine + 可选 FutuSimBroker
       循环: run_sim_cycle(...)
```

### 5.3 单轮模拟（`sim/runner.run_sim_cycle`）

| 步骤 | 说明 |
|------|------|
| 加载决策 | `source=latest` 读 `data/decisions/latest.json`；`main` 调用 `run_analysis_cycle` |
| 行情 | `fetch_market_data` → 正股快照 + 期权报价 |
| 挂单 | `process_pending_orders` — hybrid 模式下 HOLD 等触发价 |
| 执行建议 | `apply_recommendations` — 正股买卖、卖 Call/Put、ROLL |
| 估值 | `mark_to_market` |
| 持久化 | `save_snapshot` → `snapshots.jsonl`、`metrics.json`（含 Sharpe、最大回撤等） |

**数据目录：** `data/sim/`（`portfolio.json`、`trades.jsonl`、`snapshots.jsonl`、`metrics.json`）

**绩效指标模块：** `sim/metrics.py` — 从 `snapshots.jsonl` 净值序列计算 Sharpe、Sortino、最大回撤、胜率、盈亏比。

---

## 六、架构分层

```
┌─────────────────────────────────────────────────────────┐
│  cli/          命令行入口（analyze / sim）                 │
├─────────────────────────────────────────────────────────┤
│  pipeline/     分析主流程编排                             │
│  sim/runner    模拟主流程编排                             │
├─────────────────────────────────────────────────────────┤
│  analysis/     正股分析、组合 payload 构建                 │
│  decision/     AI / 规则决策、校验、存储                   │
│  planning/     正股/期权交易计划                           │
│  risk/         波动率 + 相关性动态限仓                     │
│  backtest/     信号级历史回测（规则引擎）                  │
├─────────────────────────────────────────────────────────┤
│  indicators/   技术指标、IV Rank、五策略集成               │
│  strategy/     分层策略、波段信号                          │
│  llm/          多提供商 LLM 客户端                         │
│  domain/       持仓领域模型                               │
│  market/       港股时段、整手、费用估算                    │
│  history/      成交历史缓存                               │
├─────────────────────────────────────────────────────────┤
│  brokers/futu/ Futu OpenD API 封装                       │
├─────────────────────────────────────────────────────────┤
│  config/       环境变量、SYSTEM_PROMPT                    │
│  utils/        日志、safe_float                         │
└─────────────────────────────────────────────────────────┘
```

**依赖原则：** 上层可调用下层；`domain` / `strategy` / `market` 不依赖 Futu 与网络。

---

## 七、Futu OpenD API 调用清单

以下均通过 `futu-api` Python SDK，上下文为 `OpenQuoteContext`（行情）或 `OpenSecTradeContext`（交易）。

| SDK 方法 | 封装位置 | 用途 |
|----------|----------|------|
| `position_list_query` | `brokers/futu/positions.get_position_list` | 港股实盘持仓 |
| `unlock_trade` | `brokers/futu/positions.maybe_unlock_trade` | 交易解锁 |
| `get_market_snapshot` | `brokers/futu/quotes.fetch_snapshot_map` | 正股现价、每手股数 |
| `request_history_kline` | `indicators/technical`、`backtest/signals` | 日K/周K（前复权）；经 `indicators/kline_cache` 短期缓存 |
| `get_option_expiration_date` | `brokers/futu/options.scan_sell_option_candidates` | 期权到期日列表 |
| `get_option_chain` | 同上 | 指定到期日的期权链 |
| `get_option_quote` | `brokers/futu/options.fetch_option_metrics` 等 | 期权 IV、Delta、Theta |
| `history_deal_list_query` | `history/trades._fetch_history_deals_between` | 历史成交 |
| `accinfo_query` | `sim/runner.init_mirror_portfolio` | 镜像初始化时读现金 |
| `get_acc_list` | `sim/broker.FutuSimBroker` | 解析模拟账户 ID |
| `place_order` | `sim/broker.FutuSimBroker` | 可选同步模拟盘下单 |

官方文档：<https://openapi.futunn.com/>

---

## 八、LLM API（多提供商）

| 项目 | 说明 |
|------|------|
| 封装 | `decision/ai.call_llm_decision`（`call_deepseek` 为向后兼容别名） |
| 客户端工厂 | `llm/client.create_llm_client` |
| 配置 | `llm/settings.py` — `LLM_PROVIDER`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_MAX_TOKENS` |
| 默认提供商 | `deepseek`（`DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL`） |
| 可选提供商 | `openai`、`anthropic`、`custom`（OpenAI 兼容端点） |
| 格式 | `response_format={"type": "json_object"}` |
| 输入 | 全量 payload 存档于 `data/payloads/`；API 调用前经 `analysis.slim.slim_portfolio_for_ai` 精简，含 `technical_ensemble`、`dynamic_risk` |
| 重试 | 若 `recommendations` 缺标的，最多补全 2 次 |
| 降级 | LLM 调用或校验失败时，自动 fallback 到规则引擎（`decision_source=rules_fallback`） |
| Prompt | `config/prompts.SYSTEM_PROMPT` + 动态 user prompt（含 portfolio JSON） |

### 环境变量示例

```bash
# 默认 DeepSeek（向后兼容）
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com

# 或显式指定提供商
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...

# Anthropic（通常经 OpenAI 兼容网关）
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-...
OPENAI_BASE_URL=https://your-gateway/v1
```

---

## 九、新增模块说明

### 9.1 动态风控（`risk/position_limits.py`）

- 从日 K `close_history` 计算 60 日年化波动率
- 构建持仓间收益率相关矩阵，计算与 peers 的平均相关性
- 在分层 `max_swing_position_pct` 内输出 `adjusted_max_swing_pct`
- 写入 `stock["risk_limits"]` 与 `portfolio_risk.dynamic_risk`

### 9.2 五策略技术集成（`indicators/ensemble.py`）

日 K 产出 `technical_ensemble`：

| 子策略 | 权重 | 主要指标 |
|--------|------|----------|
| trend | 25% | EMA(8/21/55)、ADX |
| mean_reversion | 20% | Z-score、布林、RSI |
| momentum | 25% | 1/3/6 月收益、量比 |
| volatility | 15% | 历史波动率、ATR 比率 |
| stat_arb | 15% | Hurst 指数、偏度 |

### 9.3 模拟绩效指标（`sim/metrics.py`）

从 `snapshots.jsonl` 的 `total_nav` 序列计算：`sharpe_ratio`、`sortino_ratio`、`max_drawdown_pct`、`win_rate_pct`、`profit_factor` 等，合并写入 `metrics.json`。

> **说明：** 代码中另有 `analysis/analysts.py`，将技术集成、波段信号、风控结果汇总为 `analyst_signals` 写入 payload，供 LLM prompt 参考。这是实现细节，**不属于**上述四项对外优化点，文档不作展开。

---

## 十、回归测试

```bash
pytest                    # tests/ 下 60+ 项
pytest tests/test_enhancements.py -v   # 风控、集成、回测、LLM 相关
pytest tests/test_decision_validation.py -v
```

覆盖：策略信号、港股时段、整手校验、决策 schema、费用、模拟撮合、动态风控、技术集成、信号回测等**不依赖 OpenD 在线**的纯逻辑。

---

## 十一、程序化扩展示例

```python
from dotenv import load_dotenv
from futu import OpenQuoteContext, TrdMarket

from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
from futu_ai_quant.llm.client import create_llm_client
from futu_ai_quant.pipeline.cycle import run_analysis_cycle

load_dotenv()
quote = OpenQuoteContext(host="127.0.0.1", port=11111)
trade = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host="127.0.0.1", port=11111)
maybe_unlock_trade(trade)
client = create_llm_client()  # 按 LLM_PROVIDER 配置

result = run_analysis_cycle(quote, trade, client, use_ai=True, print_decision=False)
# result["decision"], result["saved_path"]

quote.close()
trade.close()
```

信号回测示例：

```python
from futu import OpenQuoteContext
from futu_ai_quant.backtest import run_signal_backtest

quote = OpenQuoteContext(host="127.0.0.1", port=11111)
report = run_signal_backtest(quote, "HK.09988", pl_ratio=-30.0)
print(report["stats"])
quote.close()
```

---

## 十二、配置项索引

详见 `futu_ai_quant/config/settings.py` 模块注释及仓库 `.env.example`。

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `ANALYSIS_INTERVAL_SEC` | `0` | `0`=按交易时段自动间隔 |
| `INTRADAY_INTERVAL_SEC` | `1800` | 盘中分析间隔（秒） |
| `OFFHOURS_INTERVAL_SEC` | `14400` | 盘外分析间隔（秒） |
| `VOLUME_FILTER` | `session_adjusted` | 日K量比过滤策略 |
| `DECISIONS_DIR` | `data/decisions` | 决策 JSON 目录 |
| `PAYLOADS_DIR` | `data/payloads` | 大模型输入存档目录 |
| `KLINE_CACHE_ENABLED` | `0` | `1` 开启 K 线缓存（默认关） |
| `KLINE_CACHE_TTL_SEC` | `0` | 日K 缓存秒数；启用时建议小于分析间隔 |
| `KLINE_WEEKLY_CACHE_TTL_SEC` | `14400` | 周K 缓存秒数（仅 `ENABLED=1` 时有效） |
| `SIM_EXECUTION_MODE` | `hybrid` | 模拟：`immediate` / `trigger` / `hybrid` |
| `TRADE_RECENT_STOCK_COUNT` | `5` | 每只正股 `recent_swing_window` 保留最近 N 笔**正股**成交 |
| `TRADE_RECENT_OPTION_COUNT` | `5` | 每只正股下保留最近 N 笔**关联期权**成交 |
| `TRADE_HISTORY_CACHE_HOURS` | `12` | 成交 YTD 缓存有效期（小时内跳过 Futu API） |
| `LLM_PROVIDER` | `deepseek` | LLM 提供商：`deepseek` / `openai` / `anthropic` / `custom` |
| `LLM_MODEL` | （空） | 留空则用提供商默认模型 |
| `LLM_API_KEY` | （空） | 通用 API Key（`custom` 时使用） |
| `LLM_TEMPERATURE` | `0.2` | LLM 温度 |
| `LLM_MAX_TOKENS` | `8192` | LLM 最大输出 token |
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key（默认提供商） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 端点 |
| `OPENAI_API_KEY` | — | OpenAI API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 或兼容网关端点 |
| `ANTHROPIC_API_KEY` | — | Anthropic API Key（经兼容网关时配合 `OPENAI_BASE_URL`） |
