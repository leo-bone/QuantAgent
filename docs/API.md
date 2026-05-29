# QuantAgent API 文档

## 概述

QuantAgent 提供 RESTful HTTP API 和 WebSocket 实时推流两种接口，用于控制 Agent 运行状态、查询数据、获取性能报告。

**Base URL:** `http://localhost:8000`  
**API 版本:** `v1`  
**认证方式:** 开发版本无需认证，生产部署建议加 Bearer Token

---

## REST API

### 系统状态

#### `GET /health`

检查 API 服务是否正常运行。

**响应示例:**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-05-28T10:00:00Z"
}
```

---

### Agent 控制

#### `GET /agent/status`

获取当前 Agent 运行状态。

**响应:**
```json
{
  "state": "monitoring",
  "mode": "paper",
  "uptime_seconds": 3600,
  "tokens_monitored": ["SOL", "BNB"],
  "chains": ["solana", "bsc"],
  "last_signal": "2026-05-28T09:55:00Z",
  "portfolio_value_usd": 10000.0,
  "pnl_today_usd": 120.5,
  "pnl_total_usd": 450.2,
  "trade_count_today": 3,
  "win_rate": 0.67,
  "running": true
}
```

**AgentState 枚举:**
| 值 | 含义 |
|---|---|
| `idle` | 空闲，未启动 |
| `monitoring` | 主循环运行中，采集数据 |
| `signal_detected` | 检测到信号，准备评估 |
| `evaluating` | 策略评估中 |
| `executing` | 正在执行交易 |
| `post_trade` | 交易后处理 |
| `error` | 错误状态 |
| `paused` | 已暂停 |

---

#### `POST /agent/start`

启动 Agent 主循环。

**请求体:**
```json
{
  "mode": "paper",
  "tokens": ["SOL", "BNB"],
  "chains": ["solana", "bsc"],
  "poll_interval": 60
}
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 否 | `"paper"`（默认）或 `"live"` |
| `tokens` | array | 否 | 监控的 token 列表，默认 `["SOL", "BNB"]` |
| `chains` | array | 否 | 监控的链，默认 `["solana", "bsc"]` |
| `poll_interval` | int | 否 | 轮询间隔秒数，默认 60 |

**响应:**
```json
{
  "success": true,
  "message": "Agent started in paper mode"
}
```

---

#### `POST /agent/stop`

优雅停止 Agent（等待当前循环完成）。

**响应:**
```json
{
  "success": true,
  "message": "Agent stopped"
}
```

---

#### `POST /agent/emergency-stop`

紧急停止 Agent，立即中止所有操作。

**响应:**
```json
{
  "success": true,
  "message": "Emergency stop activated"
}
```

---

### 投资组合

#### `GET /agent/portfolio`

获取当前投资组合持仓。

**响应:**
```json
{
  "total_usd": 10500.0,
  "cash_usd": 5000.0,
  "positions": [
    {
      "token": "SOL",
      "chain": "solana",
      "amount": 10.0,
      "entry_price": 145.0,
      "current_price": 150.0,
      "pnl_usd": 50.0,
      "pnl_pct": 3.45
    }
  ]
}
```

---

### 决策记忆

#### `GET /agent/memory`

获取最近的决策记录。

**查询参数:**
- `limit` (int): 返回条数，默认 50，最大 200

**响应:**
```json
{
  "total": 150,
  "records": [
    {
      "id": "dec_20260528_001",
      "timestamp": "2026-05-28T09:30:00Z",
      "token": "SOL",
      "chain": "solana",
      "signal_type": "bullish",
      "confidence": 0.78,
      "action": "buy",
      "amount_usd": 500.0,
      "result": "confirmed",
      "pnl_usd": 25.0,
      "reasoning": "Strong momentum + whale inflow signal"
    }
  ]
}
```

---

#### `GET /agent/performance`

获取 Agent 绩效统计。

**响应:**
```json
{
  "total_trades": 45,
  "winning_trades": 30,
  "losing_trades": 15,
  "win_rate": 0.667,
  "total_pnl_usd": 1250.0,
  "avg_pnl_per_trade": 27.78,
  "best_trade_usd": 320.0,
  "worst_trade_usd": -85.0,
  "sharpe_ratio": 1.42,
  "max_drawdown_pct": 4.2,
  "period_days": 30
}
```

---

### 市场数据

#### `GET /prices`

获取多链多 token 实时价格。

**查询参数:**
- `tokens` (string): 逗号分隔，如 `SOL,BNB,CAKE`

**响应:**
```json
{
  "prices": {
    "SOL": {"price_usd": 150.25, "chain": "solana", "updated_at": "2026-05-28T10:00:00Z"},
    "BNB": {"price_usd": 610.80, "chain": "bsc", "updated_at": "2026-05-28T10:00:00Z"}
  }
}
```

---

#### `GET /fear-greed`

获取加密市场恐贫/贪婪指数。

**响应:**
```json
{
  "value": 62,
  "classification": "Greed",
  "timestamp": "2026-05-28T00:00:00Z"
}
```

**分类说明:**
| 值范围 | 分类 |
|---|---|
| 0-24 | Extreme Fear (极度恐慌) |
| 25-44 | Fear (恐慌) |
| 45-55 | Neutral (中性) |
| 56-75 | Greed (贪婪) |
| 76-100 | Extreme Greed (极度贪婪) |

---

## WebSocket API

### `WS /ws`

实时推送 Agent 状态和交易事件。

**连接示例（Python）:**
```python
import asyncio
import websockets
import json

async def connect():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        async for message in ws:
            data = json.loads(message)
            print(data)

asyncio.run(connect())
```

**消息类型:**

#### 状态更新 (`type: "status"`)
每 10 秒推送一次：
```json
{
  "type": "status",
  "data": {
    "state": "monitoring",
    "portfolio_value_usd": 10500.0,
    "pnl_today_usd": 120.5
  },
  "timestamp": "2026-05-28T10:00:00Z"
}
```

#### 信号事件 (`type: "signal"`)
检测到交易信号时推送：
```json
{
  "type": "signal",
  "data": {
    "token": "SOL",
    "chain": "solana",
    "signal_type": "bullish",
    "confidence": 0.78,
    "sources": {
      "technical": "bullish",
      "onchain": "bullish",
      "llm": "neutral"
    }
  },
  "timestamp": "2026-05-28T10:00:00Z"
}
```

#### 交易执行事件 (`type: "trade"`)
交易完成时推送：
```json
{
  "type": "trade",
  "data": {
    "action": "buy",
    "token": "SOL",
    "chain": "solana",
    "amount_usd": 500.0,
    "tx_hash": "5KNsPFv...",
    "status": "confirmed",
    "pnl_usd": null
  },
  "timestamp": "2026-05-28T10:00:30Z"
}
```

---

## 错误码

| HTTP 状态 | 说明 |
|---|---|
| `200` | 成功 |
| `400` | 请求参数错误 |
| `404` | 资源不存在 |
| `409` | Agent 状态冲突（如重复启动） |
| `500` | 服务内部错误 |

**错误响应格式:**
```json
{
  "error": "Agent already running",
  "code": "AGENT_ALREADY_RUNNING",
  "detail": "Call /agent/stop first before starting a new session"
}
```

---

## 接口使用示例

### 完整工作流（curl）

```bash
# 1. 检查健康状态
curl http://localhost:8000/health

# 2. 启动 paper trading
curl -X POST http://localhost:8000/agent/start \
  -H "Content-Type: application/json" \
  -d '{"mode": "paper", "tokens": ["SOL", "BNB"]}'

# 3. 查看当前状态
curl http://localhost:8000/agent/status

# 4. 查看最近决策
curl "http://localhost:8000/agent/memory?limit=10"

# 5. 查看绩效
curl http://localhost:8000/agent/performance

# 6. 停止 Agent
curl -X POST http://localhost:8000/agent/stop
```

### Python SDK 示例

```python
import httpx
import asyncio

BASE = "http://localhost:8000"

async def main():
    async with httpx.AsyncClient() as client:
        # 启动 Agent
        r = await client.post(f"{BASE}/agent/start", json={"mode": "paper"})
        print("Start:", r.json())

        # 等待一个分钟
        await asyncio.sleep(60)

        # 查看状态
        r = await client.get(f"{BASE}/agent/status")
        status = r.json()
        print(f"State: {status['state']} | PnL: ${status['pnl_today_usd']:.2f}")

        # 停止
        await client.post(f"{BASE}/agent/stop")

asyncio.run(main())
```
