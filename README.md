# futu_ai_quant

港股持仓量化分析与模拟交易系统。连接本地 [Futu OpenD](https://openapi.futunn.com/) 拉取真实持仓与行情，计算多周期技术指标与期权 Greeks，调用 DeepSeek 生成结构化交易建议，并可通过 `sim_trader.py` 在本地（或 Futu 模拟盘）跟踪策略效果。

## 功能概览

### `main.py` — 量化分析与 AI 建议

1. **连接 OpenD**：行情上下文 + 港股交易上下文
2. **拉取持仓**：正股 / 期权分类，区分多空方向（含卖出 Call 卖方逻辑）
3. **技术指标**（日K + 周K 双周期）：
   - RSI(14)、布林带(20,2)
   - MACD(12,26,9)、成交量确认（20 日均量 × 1.2）
   - ATR(14) 动态触发价
4. **分层策略**：按 `pl_ratio` 分为 `deep_loss` / `moderate_loss` / `profitable`，周K定方向、日K找时机
5. **期权扫描**：14–45 天到期、Delta 0.10–0.30 的卖 Call / 卖 Put 候选，含 IV Rank
6. **交易计划**：整手股数校验、备兑张数限制、触发价区间
7. **DeepSeek**：输出覆盖全部持仓的 JSON 建议，缺失自动重试
8. **决策持久化**：保存至 `data/decisions/`

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
- 记录成交、净值快照与累计绩效

## 项目结构

```
futu_ai_quant/
├── main.py              # 主分析脚本
├── sim_trader.py        # 模拟交易脚本
├── .env                 # 环境变量（勿提交）
├── data/
│   ├── decisions/       # AI 决策 JSON（含 latest.json）
│   └── sim/             # 模拟账户、成交、绩效
│       ├── portfolio.json
│       ├── trades.jsonl
│       ├── snapshots.jsonl
│       └── metrics.json
└── README.md
```

## 环境依赖

### 系统要求

| 依赖 | 说明 |
|------|------|
| **Python** | **3.12+**（`pandas-ta` 要求，3.11 不可用） |
| **Futu OpenD** | 本地安装并登录，默认 `127.0.0.1:11111` |
| **DeepSeek API** | 用于生成交易建议 |
| **网络** | 访问 DeepSeek API；pip 安装时若遇代理问题需 `unset` 代理变量 |

### Python 包

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
2. **DeepSeek**：在 [platform.deepseek.com](https://platform.deepseek.com/) 获取 API Key
3. **（可选）Futu 模拟盘**：使用 `--backend futu` 时需在 OpenD 开通港股模拟账户

## 配置

在项目根目录创建 `.env`（**不要提交到 Git**）：

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY 等
```

`.env` 示例见仓库内 [`.env.example`](.env.example)。`data/` 目录（决策 JSON、模拟账户、成交记录）已在 `.gitignore` 中忽略，每位使用者会在本地自动生成，互不干扰。

## 快速开始

### 1. 启动 OpenD

确保富途 OpenD 已运行且账户已登录。

### 2. 运行分析（单次）

```bash
conda activate futu
cd futu_ai_quant
PYTHONUNBUFFERED=1 python -u main.py --once
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

## 输出说明

### 分析日志示例

```
[指标] HK.09988 [moderate_loss] 现价=109.3 盈亏=-33.0% 日K=HOLD 周K=HOLD MACD=bullish 量比=1.19 主信号=HOLD
[仓位] HK.09988 期权方案: 卖出 2 张 ... 120.0 Call IV Rank=100.0
```

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

## 策略逻辑摘要

| 分层 | 条件 | 主导周期 | 策略要点 |
|------|------|----------|----------|
| deep_loss | 亏损 > 50% | 周K | 小仓位波段，慎卖 Put |
| moderate_loss | 亏损 0~50% | 日K | 波段降本 + 卖 Call |
| profitable | 盈利 | 周K | 止盈 + 备兑卖 Call |

日K 波段信号需成交量确认；MACD 与 RSI/布林带冲突时降级为 HOLD。

## 常见问题

**Q: `pandas-ta` 安装失败？**  
A: 需 Python 3.12+，且 pip 时避免错误代理（`unset http_proxy https_proxy`）。

**Q: 持仓拉取失败？**  
A: 检查 OpenD 是否登录；实盘查询需在 `.env` 配置 `FUTU_TRADE_UNLOCK_PWD`。

**Q: 盘中量比偏低、信号全是 HOLD？**  
A: 日K 量比为「当日累计量 / 20 日均量」，早盘通常偏低；收盘前再跑更准确。

**Q: 模拟 ROLL 做了什么？**  
A: 先买回平旧仓，再扫描同标的远月同行权价合约开新空仓；完整盈亏需平+开都完成才体现。

## 免责声明

本项目仅供学习与研究，AI 建议不构成投资建议。实盘交易请自行判断风险，作者不对任何投资损失负责。
