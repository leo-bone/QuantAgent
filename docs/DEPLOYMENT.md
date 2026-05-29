# QuantAgent 部署指南

## 快速开始

### 本地开发环境

```bash
# 1. 克隆并进入项目
git clone <repo-url>
cd quantagent

# 2. 创建 Python 虚拟环境 (要求 Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装依赖 (如在中国网络环境，需关闭代理后安装)
pip install --no-cache-dir -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API keys 和 RPC URLs

# 5. 启动 Agent (纸交易模式)
python scripts/start_agent.py --mode paper

# 6. 启动 Dashboard (另开一个终端)
python scripts/run_dashboard.py

# 7. 启动 API Server (再开一个终端)
uvicorn quantagent.api.app:app --host 0.0.0.0 --port 8000 --reload
```

---

## 环境配置 (.env)

复制 `.env.example` 为 `.env` 并填写以下关键配置：

```env
# ─── 链 RPC 节点 ───────────────────────────────────
# Solana: 推荐 Helius (helius.dev) 或 QuickNode
SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY

# BSC: 官方节点或 QuickNode
BSC_RPC_URL=https://bsc-dataseed1.binance.org/

# ─── API 密钥 ────────────────────────────────────────
# OpenAI (LLM 分析)
OPENAI_API_KEY=sk-...

# Anthropic (可选，LLM fallback)
ANTHROPIC_API_KEY=sk-ant-...

# Birdeye (Solana OHLCV，可选，免费 100 req/day)
BIRDEYE_API_KEY=your_birdeye_key

# ─── 钱包配置 (实盘必须，纸交易可留空) ─────────────
SOLANA_WALLET_ADDRESS=your_solana_wallet_public_key
SOLANA_PRIVATE_KEY=your_base58_private_key      # ⚠️ 不要泄露

BSC_WALLET_ADDRESS=0xYourBSCWalletAddress
BSC_PRIVATE_KEY=0xYourPrivateKeyHex             # ⚠️ 不要泄露

# ─── 交易模式 ────────────────────────────────────────
TRADING_MODE=paper      # paper 或 live

# ─── 风险参数 ────────────────────────────────────────
MAX_POSITION_SIZE_PCT=0.1      # 单笔最大仓位 10%
DAILY_LOSS_LIMIT_PCT=0.05      # 日亏损上限 5%
MAX_DRAWDOWN_PCT=0.15          # 最大回撤 15%
INITIAL_CAPITAL_USD=10000      # 模拟本金
```

---

## 生产部署

### Docker 单容器部署

**Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY . .

# 暴露端口
EXPOSE 8000 8050

# 默认启动 API + Dashboard
CMD ["sh", "-c", "uvicorn quantagent.api.app:app --host 0.0.0.0 --port 8000 & python scripts/run_dashboard.py & python scripts/start_agent.py --mode paper"]
```

**构建和运行:**
```bash
docker build -t quantagent:latest .
docker run -d \
  --name quantagent \
  -p 8000:8000 \
  -p 8050:8050 \
  --env-file .env \
  --restart unless-stopped \
  quantagent:latest
```

---

### Docker Compose 多服务部署

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  agent:
    build: .
    command: python scripts/start_agent.py --mode paper
    env_file: .env
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    depends_on:
      - redis

  api:
    build: .
    command: uvicorn quantagent.api.app:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    env_file: .env
    restart: unless-stopped
    depends_on:
      - agent

  dashboard:
    build: .
    command: python scripts/run_dashboard.py
    ports:
      - "8050:8050"
    env_file: .env
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

**启动:**
```bash
docker-compose up -d
docker-compose logs -f agent  # 查看 Agent 日志
```

---

### 云服务器部署（Ubuntu 22.04）

```bash
# 1. 安装 Python 3.11
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip

# 2. 创建专用用户
sudo useradd -m -s /bin/bash quantagent
sudo su - quantagent

# 3. 部署代码
git clone <repo-url> ~/quantagent
cd ~/quantagent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. 配置环境
cp .env.example .env
nano .env

# 5. 创建 systemd 服务
sudo tee /etc/systemd/system/quantagent-api.service << 'EOF'
[Unit]
Description=QuantAgent API Server
After=network.target

[Service]
User=quantagent
WorkingDirectory=/home/quantagent/quantagent
EnvironmentFile=/home/quantagent/quantagent/.env
ExecStart=/home/quantagent/quantagent/.venv/bin/uvicorn quantagent.api.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/quantagent.service << 'EOF'
[Unit]
Description=QuantAgent Trading Agent
After=network.target quantagent-api.service

[Service]
User=quantagent
WorkingDirectory=/home/quantagent/quantagent
EnvironmentFile=/home/quantagent/quantagent/.env
ExecStart=/home/quantagent/quantagent/.venv/bin/python scripts/start_agent.py --mode paper
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 6. 启动服务
sudo systemctl daemon-reload
sudo systemctl enable quantagent-api quantagent
sudo systemctl start quantagent-api quantagent
sudo systemctl status quantagent
```

---

## 访问地址

| 服务 | 地址 |
|---|---|
| API Server | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/docs |
| Dashboard | http://localhost:8050 |
| WebSocket | ws://localhost:8000/ws |

---

## 日志管理

```bash
# 查看实时日志
tail -f logs/quantagent.log

# 使用 journalctl (systemd)
sudo journalctl -u quantagent -f

# 查看 Docker 日志
docker logs -f quantagent
```

日志格式示例：
```
2026-05-28 10:00:00 | INFO     | Agent state: MONITORING
2026-05-28 10:01:00 | INFO     | [SOL/solana] Price: $150.25
2026-05-28 10:01:02 | INFO     | Signals generated: technical=BULLISH(0.72), onchain=NEUTRAL(0.45)
2026-05-28 10:01:03 | INFO     | Combined: BULLISH confidence=0.65
2026-05-28 10:01:04 | INFO     | [PAPER] Swap 0.1 SOL -> USDC | expected=15.02 USDC
```

---

## 安全注意事项

⚠️ **私钥安全**
- 私钥 (`SOLANA_PRIVATE_KEY`, `BSC_PRIVATE_KEY`) 绝对不能提交到 Git
- 生产环境使用环境变量或 secrets manager（AWS Secrets Manager、Vault 等）
- 建议创建专用交易钱包，不要用主钱包

⚠️ **实盘风险**
- 首次运行必须使用 `TRADING_MODE=paper` 纸交易模式验证策略
- 实盘前回测至少 30 天数据
- 严格遵守风险参数：`MAX_POSITION_SIZE_PCT ≤ 0.1`，`DAILY_LOSS_LIMIT_PCT ≤ 0.05`

⚠️ **API 速率限制**
- CoinGecko 免费版：30 req/min
- Birdeye 免费版：100 req/day
- Jupiter API：无官方限制，但建议 5 req/s 以内

---

## 监控与告警

**推荐指标监控（Prometheus + Grafana）:**

```python
# 在 quantagent/api/app.py 集成 prometheus_fastapi_instrumentator
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)
```

**关键告警阈值:**
- Agent 停止运行 > 5 分钟 → 告警
- 日亏损 > 3% → 告警
- API 响应时间 P99 > 2s → 告警
- 内存使用 > 80% → 告警

---

## 故障排查

| 问题 | 原因 | 解决方案 |
|---|---|---|
| `pip install` 失败 | 代理配置问题 | 用 `env -u http_proxy https_proxy pip install` |
| `Connection refused` | RPC 节点不可达 | 换用付费 RPC (Helius/QuickNode) |
| `Insufficient funds` | 余额不足 | 检查钱包余额或降低 `MAX_POSITION_SIZE_PCT` |
| 信号全部 NEUTRAL | OHLCV 数据为空 | 配置 `BIRDEYE_API_KEY` 或检查网络 |
| Dashboard 无数据 | API Server 未启动 | 确认 `uvicorn` 进程在 8000 端口运行 |
