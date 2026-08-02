# futu_ai_quant

港股持仓量化分析与模拟交易系统。连接本地 [Futu OpenD](https://openapi.futunn.com/) 拉取持仓与行情，经技术指标、动态风控与五策略集成后，调用 **LLM**（默认 DeepSeek）生成结构化建议；可用本地模拟跟踪绩效。持仓分析可经 **PushPlus** 一对一推微信；自选股定时分析（无个人持仓）可推群组；日内 T+0 监控经 **Bark** 推 iPhone。

开发细节见 [docs/GUIDE.md](docs/GUIDE.md)。完整环境变量见 [.env.example](.env.example)。

---

## 功能一览

| 入口 | 作用 |
|------|------|
| `main.py` / `futu-analyze` | 持仓分析 + AI/规则决策 → `data/payloads/`、`data/decisions/`；PushPlus **一对一**（不含群） |
| `futu-watchlist` | 自选股分析（无个人持仓）→ `data/watchlist/`；港股三槽 + 交易日门禁 + PushPlus |
| `sim_trader.py` / `futu-sim` | 按决策本地模拟撮合与绩效（Sharpe、回撤等） |
| `futu-backtest` | 历史日 K 回放规则信号（不调 LLM） |
| `futu-intraday-t` | 单标的日内 T+0 实时监控 + Bark（默认华虹；OpenD `market_state` 门禁） |
| `futu-intraday-watch` | 多标的日内轮询监控 + Bark（默认阿里+腾讯；同上） |
| `futu-intraday-pair` | 同时跑上面两个；`.env` 约 2 秒热加载；非连续交易休眠 |

---

## Ubuntu 服务器部署（推荐）

按顺序完成即可跑通。系统建议 Ubuntu 22.04 / 24.04。

### 1. 安装 Python 3.12+

本项目需要 **Python 3.12+**（`pandas-ta` 不支持 3.11）。Ubuntu 上一般用 `python3`：

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git build-essential
python3.12 --version
```

### 2. 安装并登录 Futu OpenD

下载 [命令行 OpenD（Ubuntu）](https://openapi.futunn.com/futu-api-doc/opend/opend-cmd.html)，解压后同目录须有 `FutuOpenD`、`FutuOpenD.xml`、`Appdata.dat`。

编辑 `FutuOpenD.xml`（普通使用只需账号密码）：

```xml
<ip>127.0.0.1</ip>
<api_port>11111</api_port>
<login_account>牛牛号或+86手机号</login_account>
<!-- 明文，或改用 login_pwd_md5（见下方说明） -->
<login_pwd>登录密码</login_pwd>
<!-- 或改用 login_pwd_md5 -->
<login_pwd_md5>登录密码32位小写md5值</login_pwd_md5>
<lang>chs</lang>
<!-- 服务器常驻时建议抢行情权限 -->
<auto_hold_quote_right>1</auto_hold_quote_right>
```

密码可用 MD5（只填 32 位十六进制，不要带 `md5sum` 后面的 `-`）：

```bash
echo -n '你的密码' | md5sum | awk '{print $1}'
# 结果写入 login_pwd_md5，并去掉 login_pwd
```

**首次必须前台启动**（后台起无法输入短信验证码）：

```bash
cd ~/futu/FutuOpenD   # 改成实际解压目录
chmod +x FutuOpenD
./FutuOpenD
# 按提示输入手机验证码，直到登录成功、监听 11111
```

确认端口：

```bash
ss -lntp | grep 11111
```

登录成功后再挂后台（示例 systemd，路径按实际修改）：

```ini
# /etc/systemd/system/futu-opend.service
[Unit]
Description=Futu OpenD
After=network-online.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/home/你的用户名/futu/FutuOpenD
ExecStart=/home/你的用户名/futu/FutuOpenD/FutuOpenD
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now futu-opend
journalctl -u futu-opend -f
```

也可临时：`nohup ./FutuOpenD > opend.log 2>&1 &`

> **Mac + Ubuntu 同时开 OpenD：** 允许（同账号最多约 10 端），但最高档行情只能一台持有。服务器跑量化时设 `auto_hold_quote_right=1`，避免手机/桌面牛牛抢权限。

### 3. 拉取本项目并安装依赖

```bash
git clone <仓库地址> futu_ai_quant
cd futu_ai_quant
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
# 可选：安装 CLI 命令 futu-analyze / futu-sim / futu-intraday-t 等
pip install -e .
```

### 4. 配置 `.env`

```bash
cp .env.example .env
nano .env   # 或 vim
```

**必配 / 常用项（完整说明见 `.env.example`）：**

```bash
FUTU_OPEND_HOST=127.0.0.1
FUTU_OPEND_PORT=11111
DEEPSEEK_API_KEY=sk-你的key

PUSHPLUS_ENABLED=1
PUSHPLUS_TOKEN=从pushplus.plus复制的token
PUSHPLUS_TOPIC=自选群组编码          # 仅 futu-watchlist；持仓分析强制一对一

# cp watchlist_codes.example.json data/watchlist/codes.json

INTRADAY_T_CODE=HK.01347             # 实时做 T（华虹）
INTRADAY_T_CODES=HK.09988,HK.00700   # 轮询（阿里,腾讯）
BARK_ENABLED=1
BARK_DEVICE_KEY=从Bark_App复制的key  # 只填 key，不要整段 URL
```

`.env` **不要提交到 Git**。服务器需能访问 LLM API；用 PushPlus 时需访问 `www.pushplus.plus`，用 Bark 时需访问 `api.day.app`。

### 5. 验证是否跑通

OpenD 已登录、venv 已激活：

```bash
# ① 连通性：不调 LLM，只测 OpenD + 持仓/规则
PYTHONUNBUFFERED=1 python -u main.py --once --no-ai
# 成功应生成 data/payloads/latest_payload.json 与 data/decisions/latest.json

# ② AI 决策（需已配置 DEEPSEEK_API_KEY 等）
PYTHONUNBUFFERED=1 python -u main.py --once

# ③ PushPlus 微信推送测试
python -m futu_ai_quant.cli.analyze --test-pushplus          # 持仓一对一
python -m futu_ai_quant.cli.watchlist --test-pushplus        # 自选（可走群组）

# ④ 自选股立刻跑一轮（无个人持仓；需 OpenD）
# cp watchlist_codes.example.json data/watchlist/codes.json
PYTHONUNBUFFERED=1 python -u -m futu_ai_quant.cli.watchlist --once --no-ai
# 或带 LLM：
# PYTHONUNBUFFERED=1 python -u -m futu_ai_quant.cli.watchlist --once --ai

# ⑤ Bark 推送测试（需已配置 BARK_*）
python -m futu_ai_quant.cli.intraday_t --test-bark
# iPhone 收到测试通知即通路正常

# ⑥ 历史回放演练「信号 → Bark」（非交易时段也可）
python -m futu_ai_quant.cli.intraday_t --replay --code HK.09988
```

若出现 `需要手机验证码`：停掉后台 OpenD，前台 `./FutuOpenD` 完成验证后再启动服务。

### 6. 日常运行

**推荐：常驻脚本**（持仓 / 自选 / 日内做 T）

```bash
chmod +x scripts/services.sh   # 首次

./scripts/services.sh start                 # 启动全部（analyze + watchlist + intraday）
./scripts/services.sh restart
./scripts/services.sh status
./scripts/services.sh logs
./scripts/services.sh stop

# 按类型选择（空格或逗号均可）
./scripts/services.sh start analyze watchlist
./scripts/services.sh restart intraday
./scripts/services.sh stop analyze,watchlist
./scripts/services.sh logs analyze
```

| 参数 | 含义 | 日志 |
|------|------|------|
| `analyze`（别名 `holdings` / `main`） | 持仓分析 | `data/logs/analyze.log` |
| `watchlist` | 自选三槽 | `data/logs/watchlist.log` |
| `intraday`（别名 `pair`） | 日内做 T | `data/logs/intraday.log` |
| `all`（默认） | 以上全部 | |

优先使用项目内 `.venv/bin/python`。

前台调试：

```bash
source .venv/bin/activate
PYTHONUNBUFFERED=1 python -u main.py
PYTHONUNBUFFERED=1 python -u -m futu_ai_quant.cli.watchlist
PYTHONUNBUFFERED=1 python -u -m futu_ai_quant.cli.intraday_pair
```

模拟交易：

```bash
python sim_trader.py --init-mirror
python sim_trader.py --source latest --once
python sim_trader.py --report
```

---

## 本地快速命令（已装好环境时）

```bash
source .venv/bin/activate   # 或 conda activate futu

python main.py --once --no-ai
python main.py --once
python -m futu_ai_quant.cli.analyze --test-pushplus
python -m futu_ai_quant.cli.watchlist --once --no-ai
python -m futu_ai_quant.cli.watchlist --once --ai
python -m futu_ai_quant.cli.watchlist --test-pushplus
python -m futu_ai_quant.cli.intraday_t --test-bark
python sim_trader.py --source main --once
futu-backtest --code HK.09988 --pl-ratio -30
```

---

## 项目结构（简）

```
futu_ai_quant/
├── futu_ai_quant/     # 主包（config / brokers / strategy / llm / sim / cli …）
├── tests/
├── main.py            # 分析入口
├── sim_trader.py      # 模拟入口
├── requirements.txt
├── .env               # 本地配置（勿提交）
└── data/              # 运行产物（gitignore，自动创建）
```

### data/ 目录

| 目录 | 来源 | 用途 |
|------|------|------|
| `payloads/` / `decisions/` | `main.py` | 持仓分析输入/决策 |
| `watchlist/` | `futu-watchlist` | 自选 codes.json、决策与 payload（与持仓隔离） |
| `trade_history/` | `main.py` | 当年成交缓存 |
| `iv_history/` | `main.py` | IV Rank 样本 |
| `sim/` | `sim_trader.py` | 模拟账户、成交、净值、绩效 |

```
OpenD → main.py → payloads/ + decisions/
                      ↓
              sim_trader.py → data/sim/
```

---

## 策略与风控（摘要）

| 分层 | 条件 | 主导 | 要点 |
|------|------|------|------|
| deep_loss | 亏损 > 50% | 周K | 小仓位波段，慎卖 Put |
| moderate_loss | 亏损 0~50% | 日K | 波段降本 + 卖 Call |
| profitable | 盈利 | 周K | 止盈 + 备兑卖 Call |

日 K 另有 `technical_ensemble`（趋势 / 均值回归 / 动量 / 波动率 / 统计套利加权）。动态风控按波动率与相关性收紧波段比例。

---

## 开发与测试

```bash
pip install -r requirements-dev.txt
pytest
ruff check futu_ai_quant tests
```

依赖由 pip-tools 锁定：改 `requirements.in` 后执行 `pip-compile`。CI 使用 Python 3.12。

---

## 常见问题

**OpenD 报「需要手机验证码」**  
前台运行 `./FutuOpenD` 输入短信验证码；通过后再用 systemd/nohup。

**`login_pwd_md5` 提示账号密码不匹配**  
只填 32 位哈希：`echo -n '密码' | md5sum | awk '{print $1}'`；不要把哈希写进 `login_pwd`；不要复制 `md5sum` 输出末尾的 `-`。

**持仓拉取失败**  
确认 OpenD 已登录；`.env` 填写 `FUTU_TRADE_UNLOCK_PWD`。

**行情权限被抢 / 订阅异常**  
同账号多端会互踢最高档行情。服务器设 `auto_hold_quote_right=1`，避免手机端连续抢权限。

**PushPlus 微信收不到**  
检查 `PUSHPLUS_ENABLED=1`、`PUSHPLUS_TOKEN`、已实名；服务器能访问 `www.pushplus.plus`。持仓用 `analyze --test-pushplus`（一对一）；自选群组用 `watchlist --test-pushplus` 并确认 `PUSHPLUS_TOPIC` 与成员已扫码入组。

**Bark 收不到**  
检查 `BARK_ENABLED=1`、`BARK_DEVICE_KEY` 仅为 key 而非完整 URL；服务器能访问 `api.day.app`；先跑 `--test-bark`。注意：`main.py` 不发 Bark，只有日内监控 CLI 会推送。

**`pandas-ta` 安装失败**  
需 Python 3.12+；必要时 `unset http_proxy https_proxy`。

**如何切换 LLM**  
`.env` 设 `LLM_PROVIDER`（`deepseek` / `openai` / `anthropic` / `gemini` / `custom`）及对应 Key。默认模型 `deepseek-v4-flash`。

---

## 免责声明

本项目仅供学习与研究，AI / 规则建议不构成投资建议。实盘请自行判断风险。
