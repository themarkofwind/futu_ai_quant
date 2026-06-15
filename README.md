# futu_ai_quant

港股持仓量化分析与模拟交易系统。连接本地 [Futu OpenD](https://openapi.futunn.com/) 拉取真实持仓与行情，计算多周期技术指标与期权 Greeks，经**动态风控**与**五策略技术集成**预处理后，调用 **LLM**（默认 DeepSeek，可切换多提供商）生成结构化交易建议，并可通过 `sim_trader.py` 在本地（或 Futu 模拟盘）跟踪策略效果（含 Sharpe、最大回撤等风险指标）。

**开发指南（入口、执行流程、API 说明）：** [docs/GUIDE.md](docs/GUIDE.md)

## 功能概览

### `main.py` — 量化分析与 AI 建议

1. **连接 OpenD**：行情上下文 + 港股交易上下文
2. **拉取持仓**：正股 / 期权分类，区分多空方向（含卖出 Call 卖方逻辑）
3. **技术指标**（日K + 周K 双周期）：
   - RSI(14)、布林带(20,2)
   - MACD(12,26,9)、成交量确认（20 日均量 × 1.2）
   - ATR(14) 动态触发价
   - **五策略技术集成**（`technical_ensemble`）：趋势 / 均值回归 / 动量 / 波动率 / 统计套利加权投票
4. **分层策略**：按 `pl_ratio` 分为 `deep_loss` / `moderate_loss` / `profitable`，周K定方向、日K找时机
5. **动态风控**：按 60 日波动率与持仓间相关性，在分层仓位上限内进一步收紧波段比例
6. **期权扫描**：14–45 天到期、Delta 0.10–0.30 的卖 Call / 卖 Put 候选，含 IV Rank
7. **交易计划**：整手股数校验、备兑张数限制、触发价区间
8. **LLM 决策**（默认 DeepSeek，支持 OpenAI / Anthropic / 自定义兼容端点）：输出覆盖全部持仓的 JSON 建议，缺失自动重试
9. **持久化**：输入保存至 `data/payloads/`，决策保存至 `data/decisions/`

### `sim_trader.py` — 模拟交易与绩效跟踪

基于 `main.py` 建议，在本地撮合并计入港股交易成本：

| 成本项 | 默认值 |
|--------|--------|
| 佣金 | 0.03%，最低 3 HKD |
| 平台费 | 15 HKD/笔 |
| 印花税 | 卖方 0.13%（正股） |

主要能力：

- 从真实持仓镜像初始化，或指定现金开空账户
- 按决策执行正股买卖、卖权、ROLL（平旧 + 开远月）
- 期权 `contract_size` 从行情接口实时获取（如阿里 500 股/张）
- 挂单触发（`hybrid` 模式：BUY/SELL 立即成交，HOLD 等触发价）
- 可选同步 Futu 模拟盘下单（`--backend futu`）
- 记录成交、净值快照与累计绩效（含 **Sharpe、Sortino、最大回撤、胜率** 等风险指标）

### `futu-backtest` — 信号历史回测（规则引擎）

在不调用 LLM 的前提下，用历史日 K 回放波段信号，统计 5/10 日前瞻收益与胜率，用于验证规则参数：

```bash
futu-backtest --code HK.09988 --pl-ratio -30
futu-backtest --code HK.00700 --json
```

## 项目结构

```
futu_ai_quant/
├── futu_ai_quant/           # 主包（按层级拆分）
│   ├── config/              # 环境配置与 AI Prompt
│   ├── utils/               # 日志、数值工具
│   ├── market/              # 港股时段、整手、费用
│   ├── domain/              # 持仓分类与领域模型
│   ├── strategy/            # 分层策略与波段信号
│   ├── indicators/          # 技术指标、IV Rank、五策略集成
│   ├── risk/              # 波动率 + 相关性动态限仓
│   ├── backtest/          # 信号级历史回测（规则引擎）
│   ├── llm/               # 多提供商 LLM 客户端
│   ├── brokers/futu/        # Futu OpenD 接口封装
│   ├── planning/            # 正股/期权交易计划
│   ├── history/             # 当年成交缓存
│   ├── analysis/            # 正股分析、组合 payload 构建
│   ├── decision/            # 规则/AI 决策与校验
│   ├── pipeline/            # 分析主流程编排
│   ├── sim/                 # 本地模拟交易引擎与绩效指标
│   └── cli/                 # 命令行入口（analyze / sim / backtest）
├── tests/                   # 回归测试（pytest）
├── main.py                  # 兼容入口（分析）
├── sim_trader.py            # 兼容入口（模拟）
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .env                     # 环境变量（勿提交）
└── data/                    # 本地运行数据（gitignore，见下方说明）
```

## data/ 目录说明

`data/` 在 `.gitignore` 中，**不会提交到 Git**；首次运行 `main.py` / `sim_trader.py` 后自动创建。不同机器、不同账户各自独立，便于本地 review 与回溯。

### 目录总览

| 子目录 / 文件 | 产生者 | 用途 |
|---------------|--------|------|
| `payloads/` | `main.py` | **模型输入**：发给 DeepSeek / 规则引擎的完整 `portfolio_payload` |
| `decisions/` | `main.py` | **模型输出**：AI / 规则引擎生成的交易建议 |
| `trade_history/` | `main.py` | 当年实盘成交**原始缓存**（从 Futu 增量拉取） |
| `iv_history/` | `main.py` | 各正股 IV 扫描历史（用于计算 IV Rank） |
| `sim/` | `sim_trader.py` | 本地模拟账户、成交、净值快照 |

### `payloads/` — 模型输入（便于 review）

| 文件 | 说明 |
|------|------|
| `payload_YYYYMMDD_HHMMSS.json` | 单次分析快照，与当次 `decision_*.json` **同一时间戳** |
| `latest_payload.json` | 最近一次分析的输入（固定文件名，方便直接打开） |

文件结构要点：

- 顶层 `portfolio_payload`：实际送入大模型的 JSON
- `portfolio_payload.stocks[]`：每只正股的盈亏、日K/周K 指标、`technical_ensemble`、`risk_limits`、`analyst_signals`、交易计划、卖权候选等
- `portfolio_payload.stocks[].trade_history`：**按正股聚合的历史成交摘要**（非原始流水）
  - `ytd_summary`：当年买卖笔数、量、均价
  - `recent_swing_window`：正股/期权**各**保留最近 5 笔（默认）当年成交明细
  - `swing_hint`：规则生成的波段节奏提示
- `portfolio_payload.options[]`：期权持仓的 Greeks 等（有期权持仓时）

环境变量：`PAYLOADS_DIR`（默认 `data/payloads`）

### `decisions/` — 模型输出

| 文件 | 说明 |
|------|------|
| `decision_YYYYMMDD_HHMMSS.json` | 单次决策快照 |
| `latest.json` | 最近一次决策；`sim_trader.py --source latest` 读取此文件 |

文件结构要点：

- `decision`：核心内容，`recommendations[]` 为各标的的 `action`、`reasoning`、`stock_trade_plan`、`option_trade_plan`
- `payload_path`：指向同轮 `payloads/payload_*.json`，便于输入输出对照
- `analysis_id`：与 payload 文件名中的时间戳一致
- `payload_summary`：组合市值、持仓数量等摘要

环境变量：`DECISIONS_DIR`（默认 `data/decisions`）

### `trade_history/` — 成交原始缓存

| 文件 | 说明 |
|------|------|
| `deals_ytd_YYYY.json` | 当年全部成交明细（`deals[]`），缓存未过期时**不调用 Futu API** |
| `deals_index_YYYY.json` | 按标的预聚合的正股/期权成交索引，加速 `recent_swing_window` 构建 |

- 优先读本地缓存，过期后从 Futu `history_deal_list_query` 增量刷新
- 分析时按正股汇总后写入 `payload` 的 `trade_history` 字段，**原始文件与 payload 内摘要并存**

环境变量：`TRADE_HISTORY_DIR`、`TRADE_HISTORY_CACHE_HOURS`、`TRADE_RECENT_STOCK_COUNT`、`TRADE_RECENT_OPTION_COUNT`

### `iv_history/` — IV Rank 历史

| 文件 | 说明 |
|------|------|
| `HK_09988.json` 等 | 每次卖权扫描记录的代表性 IV 样本（`iv_samples[]`） |

- 累计足够次数（默认 ≥10）后计算历史 IV Rank
- 仅影响卖权候选的 `iv_rank` 标注，与模拟账户无关

环境变量：`IV_HISTORY_DIR`、`IV_HISTORY_MIN_SAMPLES`

### `sim/` — 模拟交易数据

| 文件 | 说明 |
|------|------|
| `portfolio.json` | 模拟账户：现金、正股/期权持仓、挂单队列 |
| `trades.jsonl` | 模拟成交流水（每行一笔 JSON） |
| `snapshots.jsonl` | 每轮模拟后的净值快照（每行一笔） |
| `metrics.json` | 累计绩效摘要（含 Sharpe、最大回撤等）；`sim_trader.py --report` 读取此文件 |

环境变量：`SIM_DATA_DIR`（默认 `data/sim`）

### 数据流向（简图）

```
Futu OpenD 持仓/行情/成交
        ↓
main.py 分析
        ├─→ trade_history/deals_ytd_*.json   （原始成交缓存）
        ├─→ iv_history/HK_*.json             （IV 样本）
        ├─→ payloads/payload_*.json          （模型输入，含 trade_history 摘要）
        └─→ decisions/decision_*.json        （模型输出，含 payload_path）
                ↓
sim_trader.py --source latest
        └─→ sim/portfolio.json、trades.jsonl、snapshots.jsonl、metrics.json
```

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest
ruff check futu_ai_quant tests   # 可选：与 CI 一致的 lint
```

推送至 GitHub 后，`.github/workflows/ci.yml` 会自动跑 ruff + pytest（Python 3.12）。

新功能开发后建议先跑测试再提交，覆盖策略信号、整手校验、决策 schema、模拟撮合、动态风控、技术集成、信号回测等核心逻辑。

## 环境依赖

### 系统要求

| 依赖 | 说明 |
|------|------|
| **Python** | **3.12+**（`pandas-ta` 要求，3.11 不可用） |
| **Futu OpenD** | 本地安装并登录，默认 `127.0.0.1:11111` |
| **LLM API** | 默认 DeepSeek；可切换 OpenAI / Anthropic 等（见 `.env.example`） |
| **网络** | 访问 LLM API；pip 安装时若遇代理问题需 `unset` 代理变量 |

### Python 包

```bash
pip install -r requirements.txt
```

依赖版本由 [pip-tools](https://github.com/jazzband/pip-tools) 锁定：直接依赖写在 `requirements.in`，生成 `requirements.txt`：

```bash
pip install -r requirements-dev.txt   # 含 pip-tools
pip-compile requirements.in -o requirements.txt
pip-compile requirements-dev.in -o requirements-dev.txt
```

或手动安装：

```bash
pip install futu-api pandas pandas-ta openai python-dotenv
```

推荐使用 Conda 独立环境：

```bash
conda create -n futu python=3.12 -y
conda activate futu
pip install futu-api pandas pandas-ta openai python-dotenv
```

### 外部服务

1. **Futu OpenD**：在富途牛牛中开启 OpenAPI，启动 OpenD 并保持登录
2. **LLM**：默认 [DeepSeek](https://platform.deepseek.com/)（`DEEPSEEK_API_KEY`）；亦可配置 `LLM_PROVIDER=openai` 等
3. **（可选）Futu 模拟盘**：使用 `--backend futu` 时需在 OpenD 开通港股模拟账户

## 配置

在项目根目录创建 `.env`（**不要提交到 Git**）：

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY（或 LLM_PROVIDER / OPENAI_API_KEY 等）
```

`.env` 示例见仓库内 [`.env.example`](.env.example)。`data/` 目录已在 `.gitignore` 中忽略，每位使用者本地自动生成；各子目录含义见上文 **[data/ 目录说明](#data-目录说明)**。

## 快速开始

### 1. 启动 OpenD

确保富途 OpenD 已运行且账户已登录。

### 2. 运行分析（单次）

```bash
conda activate futu
cd futu_ai_quant
pip install -e ".[dev]"   # 可选：以可编辑模式安装包
PYTHONUNBUFFERED=1 python -u main.py --once
# 或：python -m futu_ai_quant --once
# 或：futu-analyze --once

# 不调用 LLM，仅用规则引擎（无需 API Key）
python main.py --once --no-ai

# 切换 LLM 提供商（示例：OpenAI）
LLM_PROVIDER=openai LLM_MODEL=gpt-4o-mini python main.py --once
```

### 3. 常驻运行

```bash
PYTHONUNBUFFERED=1 python -u main.py
```

盘中每 30 分钟、盘外每 4 小时自动分析一轮。

### 4. 模拟交易

```bash
# 首次：用真实持仓初始化模拟账户
python sim_trader.py --init-mirror

# 按最新决策跑一轮
python sim_trader.py --source latest --once

# 分析 + 模拟一步完成
python sim_trader.py --source main --once

# 查看绩效
python sim_trader.py --report

# 同步 Futu 模拟盘下单
python sim_trader.py --backend futu --source latest --once
```

### 5. 信号历史回测（规则引擎，不调 LLM）

```bash
# 验证某标的波段规则的历史表现
futu-backtest --code HK.09988 --pl-ratio -30

# JSON 输出完整统计
futu-backtest --code HK.00700 --json
```

## 输出说明

分析产生的 JSON 文件说明见 **[data/ 目录说明](#data-目录说明)**。以下为日志与字段速览。

### 分析日志示例

```
[指标] HK.09988 [moderate_loss] 现价=109.3 盈亏=-33.0% 日K=HOLD 周K=HOLD MACD=bullish 量比=1.19 主信号=HOLD
[风控] HK.09988 波段上限 20% → 14%（波动/相关性调整）
[仓位] HK.09988 期权方案: 卖出 2 张 ... 120.0 Call IV Rank=100.0
```

### Payload 新增字段（`data/payloads/`）

| 字段 | 说明 |
|------|------|
| `stocks[].daily.technical_ensemble` | 五策略技术集成信号（bullish/bearish/neutral + 置信度） |
| `stocks[].risk_limits` | 动态限仓：`tier_max_swing_pct` → `adjusted_max_swing_pct` |
| `portfolio_risk.dynamic_risk` | 组合级波动率/相关性风控摘要 |

### AI 决策 JSON

每条 `recommendations` 包含：

- `action`：BUY / SELL / HOLD / ROLL
- `stock_trade_plan`：整手买卖数量、触发价
- `option_trade_plan`：完整合约代码、到期日、张数、权利金

### 模拟绩效 `data/sim/metrics.json`

- `latest_nav`：模拟净值
- `realized_pnl`：已实现盈亏
- `total_unrealized_pnl`：浮动盈亏
- `total_fees`：累计费用
- `sharpe_ratio` / `sortino_ratio`：风险调整后收益（基于 `snapshots.jsonl` 日收益率）
- `max_drawdown_pct` / `max_drawdown_date`：最大回撤及发生日期
- `win_rate_pct` / `profit_factor`：日胜率与盈亏比

## 策略逻辑摘要

| 分层 | 条件 | 主导周期 | 策略要点 |
|------|------|----------|----------|
| deep_loss | 亏损 > 50% | 周K | 小仓位波段，慎卖 Put |
| moderate_loss | 亏损 0~50% | 日K | 波段降本 + 卖 Call |
| profitable | 盈利 | 周K | 止盈 + 备兑卖 Call |

日K 波段信号需成交量确认；MACD 与 RSI/布林带冲突时降级为 HOLD。

**动态风控**：在分层 `max_swing_position_pct` 内，高波动标的与组合高相关性持仓会进一步收紧波段比例（见 `risk_limits`）。

**技术集成**：日 K 额外产出 `technical_ensemble`，五类子策略（趋势 25%、均值回归 20%、动量 25%、波动率 15%、统计套利 15%）加权投票，供 LLM 参考。

## 常见问题

**Q: `pandas-ta` 安装失败？**  
A: 需 Python 3.12+，且 pip 时避免错误代理（`unset http_proxy https_proxy`）。

**Q: 持仓拉取失败？**  
A: 检查 OpenD 是否登录；实盘查询需在 `.env` 配置 `FUTU_TRADE_UNLOCK_PWD`。

**Q: 盘中量比偏低、信号全是 HOLD？**  
A: 日K 量比为「当日累计量 / 20 日均量」，早盘通常偏低；收盘前再跑更准确。

**Q: 模拟 ROLL 做了什么？**  
A: 先买回平旧仓，再扫描同标的远月同行权价合约开新空仓；完整盈亏需平+开都完成才体现。

**Q: 如何切换 LLM 提供商？**  
A: 在 `.env` 设置 `LLM_PROVIDER`（`deepseek` / `openai` / `anthropic` / `custom`）及对应 API Key；`LLM_MODEL` 留空则用提供商默认模型。仍兼容原有 `DEEPSEEK_API_KEY`。

**Q: `futu-backtest` 与 `sim_trader` 有什么区别？**  
A: `futu-backtest` 用历史 K 线回放**规则信号**的前瞻收益，不调 LLM；`sim_trader` 是前向纸面交易，跟踪实际决策的撮合与净值。

## 免责声明

本项目仅供学习与研究，AI 建议不构成投资建议。实盘交易请自行判断风险，作者不对任何投资损失负责。
