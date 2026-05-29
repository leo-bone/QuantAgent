# QuantAgent 项目长期记忆

## 项目概况
- 目录：`/Users/leo/WorkBuddy/2026-05-27-23-00-43/`
- 技术栈：Python 3.11 + FastAPI + Dash + Solana + BSC + OpenAI/Anthropic
- 虚拟环境：`.venv/`

## MVP 已完成的模块（全部验证通过）
| 层 | 文件 | 状态 |
|---|---|---|
| 数据模型 | common/models.py | ✅ |
| 链抽象 | data/collector.py | ✅ |
| Solana 数据 | data/solana_collector.py | ✅ |
| BSC 数据 | data/bsc_collector.py | ✅ |
| DEX 聚合 | data/dex_collector.py | ✅ |
| 鲸鱼监控 | data/whale_monitor.py | ✅ |
| OHLCV 提供者 | data/ohlcv_provider.py | ✅ |
| 技术指标 | signal/technical.py（纯numpy/pandas） | ✅ |
| LLM 分析 | signal/llm_analyzer.py（async + circuit breaker） | ✅ |
| 信号融合 | signal/combiner.py（自适应权重） | ✅ |
| 自适应权重 | signal/adaptive_weights.py | ✅ |
| 动量策略 | strategy/momentum.py | ✅ |
| 风险管理 | strategy/risk_manager.py | ✅ |
| Agent主循环 | agent/quant_agent.py | ✅ |
| 记忆存储 | agent/memory.py | ✅ |
| Solana执行 | execution/solana_executor.py | ✅ |
| BSC执行 | execution/bsc_executor.py | ✅ |
| FastAPI | api/app.py（+metrics +backtest +evolution） | ✅ |
| Dash Dashboard | dashboard/app.py（实时数据+控制面板） | ✅ |
| 数据库持久化 | data/database.py（SQLAlchemy async） | ✅ |
| 回测框架 | backtest/engine.py + runner.py | ✅ |
| 熔断器 | infrastructure/circuit_breaker.py | ✅ |
| 健康检查 | infrastructure/health_check.py | ✅ |
| 运行指标 | infrastructure/metrics.py | ✅ |
| **策略基因组** | **evolution/genome.py** | ✅ NEW |
| **适应度评估** | **evolution/fitness.py** | ✅ NEW |
| **遗传操作** | **evolution/operators.py** | ✅ NEW |
| **进化引擎** | **evolution/engine.py** | ✅ NEW |
| **自省学习** | **evolution/reflection.py** | ✅ NEW |

## 自进化架构核心逻辑
```
Trade → Reflect → Suggest Mutations → Evolve → Better Strategy → Deploy
  ↑                                                        │
  └────────────── Continuous Loop ──────────────────────────┘
```
- 25个策略基因（信号权重×3 + 技术指标×9 + 风险参数×6 + 入场规则×4 + 出场规则×3）
- 适应度 = Sharpe × 一致性 × 回撤惩罚 × 频率因子（多目标）
- 遗传操作：高斯/边界突变、BLX-α交叉、锦标赛选择、自适应突变率
- 自省双通道：LLM 分析 → 定向突变建议 / 规则引擎 fallback
- 自动部署：fitness > threshold 时自动替换活跃策略
- 状态持久化：种群+进化历史保存到 data/evolution/

## 重要技术选型记录
- technical.py 改为纯 numpy/pandas，不依赖 pandas_ta（Python 3.11 不兼容）
- OHLCV: Solana → Birdeye（付费），BSC → CoinGecko，均有 fallback
- 执行层: Solana 用 solders + Jupiter swap tx，BSC 用 web3.py + PancakeSwap Router V2
- pip install 需 `env -u http_proxy ... pip install`（系统有 Clash 代理）
- LLM 异步化：AsyncOpenAI + AsyncAnthropic，避免阻塞 event loop
- 信号权重：自适应权重引擎（基于滚动窗口准确率），替代固定 0.3/0.4/0.3
- 回测引擎：事件驱动，逐 candle 重放，支持 Sharpe/Sortino/MaxDD/WinRate/Calmar
- Solana mint 地址已验证（通过 Solscan/Solana Explorer）
- 自进化 = 遗传算法 + LLM自省 + 自动部署的混合闭环

## 踩坑记录
- **Clash 代理是最大坑**：系统 env `http_proxy=http://127.0.0.1:59943` 导致 requests/httpx 所有请求走代理
  - 修复：httpx 加 `proxy=None`，requests 加 `proxies={"no_proxy": True}`，启动脚本 unset 代理变量
  - 启动脚本额外设 `os.environ["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"`
- 浏览器必须访问 `http://127.0.0.1:8050`，**不要用 localhost**（会被 Clash DNS 劫持）
- CoinGecko token ID ≠ symbol（SOL→solana, BNB→binancecoin, CAKE→pancakeswap-token）
- CoinGecko OHLC endpoint 不含 volume，免费 tier 30 req/min
- **Solana token decimals 不全是 9**：USDC=6, USDT=6, BONK=5, JUP=6, WIF=6
- **BSC USDT decimals=18**（不同于 Ethereum 的 6）

## 启动方式
```bash
cd /Users/leo/WorkBuddy/2026-05-27-23-00-43
source .venv/bin/activate
./scripts/start.sh        # 全栈一键启动
# 或单独：python scripts/start_agent.py --mode paper
```
⚠️ 浏览器访问 Dashboard 用 http://127.0.0.1:8050

## Grant 申请状态
- 完成全面代码审计（P0/P1/P2 共 15 项）
- P0 全部修复（mint 地址、decimals）
- P1 核心能力新增（自适应权重、回测框架、熔断器、LLM异步化、可观测性）
- P2 代码质量修复（requirements清理、SQL兼容性）
- **自进化引擎完整实现**（基因组+适应度+遗传算子+进化主循环+自省学习）

## 下一步（Phase 2→3）
- 实盘模式：安装 solders + base58，配置 SOLANA_PRIVATE_KEY
- 接入 Birdeye API key 获取完整 OHLCV
- 鲸鱼监控接入 Helius/QuickNode（当前返回空列表）
- 资金费率接入 Coinglass API（当前硬编码 stub）
- 添加均值回归策略
- PostgreSQL 生产部署
- 进化引擎定时触发（automated periodic evolution）
