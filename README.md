# QuantAgent - AI量化Agent即服务

AI驱动的链上量化交易Agent，支持Solana+BSC双链，自主监控、分析、执行交易。

## 架构

```
┌─────────────────────────────────────────┐
│          Dashboard / API Layer           │  FastAPI + Dash
├─────────────────────────────────────────┤
│          Agent Orchestrator              │  自主决策引擎
├──────────────┬──────────────────────────┤
│  Signal      │  Strategy    │ Execution │  三引擎核心
│  Engine      │  Engine      │ Engine    │
├──────────────┴──────────────┴──────────┤
│          Data Aggregation Layer          │  链上+链下数据
├─────────────────────────────────────────┤
│          Chain Abstraction Layer         │  Solana + BSC
└─────────────────────────────────────────┘
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置
cp .env.example .env
# 编辑 .env 填入 API keys

# 启动Agent（模拟模式）
python scripts/start_agent.py --mode paper

# 启动Dashboard
python -m quantagent.dashboard.app

# 启动API服务
uvicorn quantagent.api.app:app --reload
```

## 安全警告

**⚠️ 本软件涉及链上交易，使用真实私钥意味着真实资金风险。**

- 首次使用务必以 `--mode paper` 模拟模式运行
- 私钥存储在加密文件中，运行时解密到内存
- 设置合理的交易限额和止损
- 本软件不构成投资建议

### ERC-20 授权额度

swap 前需要给 PancakeSwap router 授权（`approve`），**授权额度就是「router 被攻破时最多能划走多少」**。
默认按单笔交易精确授权，不留残余敞口：

| `BSC_ALLOWANCE_MODE` | 授权量 | 残余敞口 | 适用 |
|---|---|---|---|
| `exact`（默认） | 恰好本笔所需 | 0（swap 花光，额度自动归零） | 推荐 |
| `capped` | `amount × BSC_ALLOWANCE_CAP_MULTIPLE`（默认 2 倍） | 最多多一笔 | 高频交易、想省 approve 的 gas |
| `unlimited` | `type(uint256).max` | **该代币全部余额** | 明确接受风险才用，启动时打警告 |

`BSC_REVOKE_AFTER_SWAP=true` 可在成交后把残留授权归零（多花一笔 gas）。
未知模式会直接抛错，不会静默降级。

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| API | FastAPI |
| 可视化 | Dash + Plotly |
| 数据存储 | PostgreSQL + Redis |
| AI-规则层 | pandas, ta-lib, numpy |
| AI-推理层 | OpenAI / Claude API |
| Solana | solana-py, Jupiter SDK |
| BSC | web3.py, PancakeSwap |

## License

MIT
