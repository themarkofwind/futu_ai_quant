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
   - `DEEPSEEK_API_KEY` — 分析走 AI 时必填；可用 `--no-ai` 跳过
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

# 不调用 DeepSeek，仅用规则引擎（无需 API Key）
python main.py --once --no-ai

# 常驻：盘中约 30 分钟、盘外约 4 小时一轮（见 market/session.py）
python main.py

# 等价
python -m futu_ai_quant --once
```

### 3.2 启动后执行流程

```
cli/analyze.main()
  ├─ load_dotenv()                    # 加载 .env
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
| 4 | `history/trades` | `load_ytd_trade_history` | 当年成交（本地缓存 + 增量 API） |
| 5 | `analysis/stock` | `compute_stock_indicators` | 日K/周K指标、卖权扫描、交易计划 |
| 6 | `history/trades` | `attach_trade_history_to_stocks` | 挂载 YTD / 近两周成交 |
| 7 | `brokers/futu/options` | `fetch_option_metrics` | 持仓期权 IV / Greeks |
| 8 | `analysis/portfolio` | `attach_stock_option_context` | 正股挂载已有期权与建议卖权 |
| 9 | `analysis/portfolio` | `build_portfolio_payload` | 组装发给 AI 的 JSON |
| 10 | `decision/ai` 或 `decision/rules` | `call_deepseek` / `build_rules_decision` | 生成 recommendations |
| 11 | `decision/validation` | `validate_decision_schema` | 整手、字段完整性校验 |
| 12 | `decision/storage` | `save_analysis_artifacts` | 输入写入 `data/payloads/`，输出写入 `data/decisions/` |

**输出：**

- 输入 review：`data/payloads/payload_YYYYMMDD_HHMMSS.json`、`latest_payload.json`
- 决策输出：`data/decisions/decision_YYYYMMDD_HHMMSS.json`、`latest.json`（含 `payload_path` 关联输入）

---

## 四、模拟交易（sim_trader / futu-sim）

### 4.1 常用命令

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

### 4.2 启动后执行流程

```
cli/sim.main()
  ├─ --report → sim/io.print_report() 后直接退出
  ├─ --init-mirror / --init-cash → 初始化 data/sim/portfolio.json
  └─ 否则:
       PaperPortfolio.load()
       LocalSimEngine + 可选 FutuSimBroker
       循环: run_sim_cycle(...)
```

### 4.3 单轮模拟（`sim/runner.run_sim_cycle`）

| 步骤 | 说明 |
|------|------|
| 加载决策 | `source=latest` 读 `data/decisions/latest.json`；`main` 调用 `run_analysis_cycle` |
| 行情 | `fetch_market_data` → 正股快照 + 期权报价 |
| 挂单 | `process_pending_orders` — hybrid 模式下 HOLD 等触发价 |
| 执行建议 | `apply_recommendations` — 正股买卖、卖 Call/Put、ROLL |
| 估值 | `mark_to_market` |
| 持久化 | `save_snapshot` → `snapshots.jsonl`、`metrics.json` |

**数据目录：** `data/sim/`（`portfolio.json`、`trades.jsonl`、`snapshots.jsonl`、`metrics.json`）

---

## 五、架构分层

```
┌─────────────────────────────────────────────────────────┐
│  cli/          命令行入口（analyze / sim）                 │
├─────────────────────────────────────────────────────────┤
│  pipeline/     分析主流程编排                             │
│  sim/runner    模拟主流程编排                             │
├─────────────────────────────────────────────────────────┤
│  analysis/     正股分析、组合 payload                      │
│  decision/     AI / 规则决策、校验、存储                   │
│  planning/     正股/期权交易计划                           │
├─────────────────────────────────────────────────────────┤
│  indicators/   技术指标、IV Rank                          │
│  strategy/     分层策略、波段信号                          │
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

## 六、Futu OpenD API 调用清单

以下均通过 `futu-api` Python SDK，上下文为 `OpenQuoteContext`（行情）或 `OpenSecTradeContext`（交易）。

| SDK 方法 | 封装位置 | 用途 |
|----------|----------|------|
| `position_list_query` | `brokers/futu/positions.get_position_list` | 港股实盘持仓 |
| `unlock_trade` | `brokers/futu/positions.maybe_unlock_trade` | 交易解锁 |
| `get_market_snapshot` | `brokers/futu/quotes.fetch_snapshot_map` | 正股现价、每手股数 |
| `request_history_kline` | `indicators/technical.compute_timeframe_indicators` | 日K/周K（前复权） |
| `get_option_expiration_date` | `brokers/futu/options.scan_sell_option_candidates` | 期权到期日列表 |
| `get_option_chain` | 同上 | 指定到期日的期权链 |
| `get_option_quote` | `brokers/futu/options.fetch_option_metrics` 等 | 期权 IV、Delta、Theta |
| `history_deal_list_query` | `history/trades._fetch_history_deals_between` | 历史成交 |
| `accinfo_query` | `sim/runner.init_mirror_portfolio` | 镜像初始化时读现金 |
| `get_acc_list` | `sim/broker.FutuSimBroker` | 解析模拟账户 ID |
| `place_order` | `sim/broker.FutuSimBroker` | 可选同步模拟盘下单 |

官方文档：<https://openapi.futunn.com/>

---

## 七、DeepSeek API

| 项目 | 说明 |
|------|------|
| 封装 | `decision/ai.call_deepseek` |
| 客户端 | `openai.OpenAI`（`base_url` 默认 `https://api.deepseek.com`） |
| 模型 | `deepseek-chat` |
| 格式 | `response_format={"type": "json_object"}` |
| 重试 | 若 `recommendations` 缺标的，最多补全 2 次 |
| Prompt | `config/prompts.SYSTEM_PROMPT` + 动态 user prompt（含 portfolio JSON） |

---

## 八、回归测试

```bash
pytest                    # tests/ 下 34+ 项
pytest tests/test_decision_validation.py -v
```

覆盖：策略信号、港股时段、整手校验、决策 schema、费用、模拟撮合等**不依赖 OpenD 在线**的纯逻辑。

---

## 九、程序化扩展示例

```python
from dotenv import load_dotenv
from futu import OpenQuoteContext, TrdMarket
from openai import OpenAI

from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
from futu_ai_quant.pipeline.cycle import run_analysis_cycle

load_dotenv()
quote = OpenQuoteContext(host="127.0.0.1", port=11111)
trade = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host="127.0.0.1", port=11111)
maybe_unlock_trade(trade)
client = OpenAI()  # 需 DEEPSEEK_API_KEY

result = run_analysis_cycle(quote, trade, client, use_ai=True, print_decision=False)
# result["decision"], result["saved_path"]

quote.close()
trade.close()
```

---

## 十、配置项索引

详见 `futu_ai_quant/config/settings.py` 模块注释及仓库 `.env.example`。

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `ANALYSIS_INTERVAL_SEC` | `0` | `0`=按交易时段自动间隔 |
| `INTRADAY_INTERVAL_SEC` | `1800` | 盘中分析间隔（秒） |
| `OFFHOURS_INTERVAL_SEC` | `14400` | 盘外分析间隔（秒） |
| `VOLUME_FILTER` | `session_adjusted` | 日K量比过滤策略 |
| `DECISIONS_DIR` | `data/decisions` | 决策 JSON 目录 |
| `PAYLOADS_DIR` | `data/payloads` | 大模型输入存档目录 |
| `SIM_EXECUTION_MODE` | `hybrid` | 模拟：`immediate` / `trigger` / `hybrid` |
| `SIM_BACKEND` | `local` | 模拟：`local` / `futu` / `both` |
