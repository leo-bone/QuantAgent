# QuantAgent 架构设计文档

## 系统概述

QuantAgent 是一个 AI 驱动的链上量化交易 Agent，支持 Solana + BSC 双链。系统采用 6 层架构设计，从底到上依次为：

```
┌─────────────────────────────────────────────┐
│           Dashboard / API Layer               │  FastAPI + Dash
├─────────────────────────────────────────────┤
│           Agent Orchestrator                  │  自主决策引擎
├──────────┬──────────────┬────────────────────┤
│  Signal  │   Strategy   │    Execution       │  三引擎核心
│  Engine  │   Engine     │    Engine           │
├──────────┴──────────────┴────────────────────┤
│           Data Aggregation Layer              │  链上+链下数据
├─────────────────────────────────────────────┤
│           Chain Abstraction Layer             │  Solana + BSC
└─────────────────────────────────────────────┘
```

## 核心流程

```
数据采集 → 信号生成 → 信号融合 → 策略评估 → 风控检查 → 执行交易 → 反馈学习
```

### 详细流程

1. **数据采集**: DexCollector 从 Jupiter/PancakeSwap 获取价格，从链上获取交易数据
2. **信号生成**: 三层信号源并行工作
   - 技术信号：RSI/MACD/布林带/成交量（本地计算，低频）
   - 链上信号：鲸鱼异动/流动性变化（事件驱动）
   - LLM信号：综合推理（按需调用，高成本）
3. **信号融合**: SignalCombiner 加权融合，冲突时降权低置信度信号
4. **策略评估**: 策略引擎根据融合信号和风控输出交易动作
5. **风控检查**: RiskManager 检查限额、回撤、紧急停止
6. **执行交易**: Chain Provider 通过 DEX 执行 swap
7. **反馈学习**: 记录决策和结果到 Memory Store

## 数据模型

### 核心实体

| 模型 | 说明 |
|------|------|
| `Signal` | 单一信号源的交易信号 |
| `CombinedSignal` | 多源融合后的综合信号 |
| `StrategyAction` | 策略输出的交易动作 |
| `RiskAssessment` | 风控评估结果 |
| `Order` | 交易订单 |
| `AgentMemory` | Agent 决策记忆 |

### 枚举类型

| 枚举 | 值 | 说明 |
|------|------|------|
| `Chain` | solana, bsc | 支持的区块链 |
| `SignalType` | bullish, bearish, neutral | 信号方向 |
| `SignalSource` | technical, onchain, llm | 信号来源 |
| `ActionType` | buy, sell, hold, close | 交易动作 |
| `AgentState` | idle, monitoring, ... | Agent 状态机 |

## 链抽象层

### 接口设计

```python
class ChainProvider(ABC):
    async def get_price(token) -> float
    async def get_ticker(token) -> Ticker
    async def get_ohlcv(token, interval, limit) -> list[OHLCV]
    async def get_balance(token, wallet) -> float
    async def get_swap_quote(from, to, amount) -> dict
    async def execute_swap(from, to, amount) -> Order
    async def get_large_transfers(token, min_usd) -> list[dict]
    async def get_liquidity_pool_info(token) -> dict
```

### Solana 实现
- 价格：Jupiter Price API (v6)
- 交易：Jupiter Aggregator SDK
- 鲸鱼监控：Helius DAS API（待集成）
- RPC：QuickNode/Helius

### BSC 实现
- 价格：CoinGecko API（主）/ PancakeSwap Router（备）
- 交易：PancakeSwap Router 合约
- 鲸鱼监控：BSCScan API（待集成）
- RPC：BNB48/RPC

## 信号引擎

### 混合架构

| 层级 | 更新频率 | 成本 | 精度 |
|------|----------|------|------|
| 技术信号 | 每分钟 | 零（本地计算） | 中等 |
| 链上信号 | 事件驱动 | RPC 调用 | 高 |
| LLM信号 | 每小时/重大事件 | $0.01-0.05/次 | 语境理解强 |

### 信号融合算法

```
score(direction) = Σ(weight_source × confidence_signal × conflict_adjustment)
final_direction = argmax(score)
final_confidence = score(best) / sum(all_scores)
```

冲突处理：当某信号与更高置信度的对立信号冲突时，按比例降权。

## 策略引擎

### 动量策略 (MomentumStrategy)

- 入场条件：融合信号置信度 > 0.55 且方向为 bullish
- 仓位计算：`position = max_position × confidence × risk_multiplier`
- 止损：基于 ATR × 1.5 或风险引擎建议值
- 止盈：2:1 风险回报比

## 风控系统

### 硬限制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 最大单笔仓位 | $1,000 | 单笔交易最大金额 |
| 日亏损上限 | $500 | 超过自动暂停 |
| 最大回撤 | 15% | 超过触发紧急停止 |
| 最大滑点 | 1% | 超过取消交易 |

### 安全机制

- **紧急停止**: 日亏损超1.5倍限制自动触发
- **模拟模式**: `--mode paper` 不执行真实交易
- **私钥加密**: AES-256 加密存储，运行时解密到内存

## Agent 状态机

```
IDLE ──→ MONITORING ──→ SIGNAL_DETECTED ──→ EVALUATING ──→ EXECUTING ──→ POST_TRADE
  ↑                                                                               │
  └───────────────────────────────────────────────────────────────────────────────┘
                                   ERROR ← (any state)
```

## 后续演进 (Phase 2/3)

### Agent 经济层
- Agent-to-Agent 服务发现与信誉
- KYA (Know Your Agent) 身份体系
- x402 微支付协议集成
- 策略 NFA 化（铸造成非同质化 Agent）

### 基础设施增强
- PostgreSQL 时序数据持久化
- Redis Pub/Sub 实时数据推送
- Celery 异步任务队列
- Docker 容器化部署

### 数据源扩展
- Birdeye OHLCV API
- Helius DAS API（Solana 鲸鱼监控）
- BSCScan API（BSC 鲸鱼监控）
- Coinglass 资金费率
- DexScreener 流动性数据
