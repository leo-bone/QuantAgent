"""QuantAgent common data models - the backbone of the entire system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─── Enums ───

class Chain(str, Enum):
    SOLANA = "solana"
    BSC = "bsc"


class SignalType(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalSource(str, Enum):
    TECHNICAL = "technical"
    ONCHAIN = "onchain"
    LLM = "llm"


class ActionType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"


class AgentState(str, Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    SIGNAL_DETECTED = "signal_detected"
    EVALUATING = "evaluating"
    EXECUTING = "executing"
    POST_TRADE = "post_trade"
    ERROR = "error"
    PAUSED = "paused"


class TradeStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ─── Token ───

class Token(BaseModel):
    """Represents a blockchain token."""
    symbol: str
    address: str
    chain: Chain
    decimals: int = 18
    name: str = ""


# ─── Market Data ───

class OHLCV(BaseModel):
    """Open-High-Low-Close-Volume candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    token: str  # symbol
    chain: Chain
    interval: str = "1h"  # 1m, 5m, 15m, 1h, 4h, 1d


class Ticker(BaseModel):
    """Real-time price ticker."""
    token: str
    chain: Chain
    price_usd: float
    price_change_24h: float = 0.0
    volume_24h: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Signals ───

class Signal(BaseModel):
    """A trading signal from any source."""
    source: SignalSource
    signal_type: SignalType
    token: str
    chain: Chain
    confidence: float = Field(ge=0.0, le=1.0)
    indicator: str = ""  # e.g. "RSI", "whale_transfer", "llm_analysis"
    value: Any = None  # raw indicator value
    description: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CombinedSignal(BaseModel):
    """Fused signal from multiple sources."""
    token: str
    chain: Chain
    signal_type: SignalType
    confidence: float = Field(ge=0.0, le=1.0)
    technical_signals: list[Signal] = []
    onchain_signals: list[Signal] = []
    llm_signal: Optional[Signal] = None
    llm_reasoning: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── Strategy ───

class StrategyAction(BaseModel):
    """Action output from a strategy evaluation."""
    action: ActionType
    token: str
    chain: Chain
    position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasoning: str = ""


class RiskAssessment(BaseModel):
    """Risk assessment for a potential trade."""
    risk_score: float = Field(ge=0.0, le=1.0)  # 0=safe, 1=dangerous
    max_position_usd: float
    suggested_stop_loss_pct: float
    volatility_24h: float = 0.0
    liquidity_score: float = Field(ge=0.0, le=1.0, default=1.0)
    warnings: list[str] = []


# ─── Execution ───

class Order(BaseModel):
    """A trade order."""
    id: str = ""
    token: str
    chain: Chain
    action: ActionType
    order_type: OrderType
    amount_usd: float
    price_limit: Optional[float] = None  # for limit orders
    slippage_bps: int = 100
    status: TradeStatus = TradeStatus.PENDING
    tx_hash: str = ""
    executed_price: Optional[float] = None
    executed_amount: Optional[float] = None
    gas_fee_usd: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TradeRecord(BaseModel):
    """A completed trade record."""
    id: str = ""
    order: Order
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    fees_usd: float = 0.0
    notes: str = ""


# ─── Agent ───

class AgentConfig(BaseModel):
    """Configuration for a running agent."""
    name: str = "QuantAgent-01"
    chains: list[Chain] = [Chain.SOLANA, Chain.BSC]
    tokens_to_monitor: list[str] = ["SOL", "BNB", "BTC", "ETH", "USDC"]
    strategy: str = "momentum"
    trading_mode: str = "paper"
    max_position_size_usd: float = 1000.0
    max_daily_loss_usd: float = 500.0


class AgentStatus(BaseModel):
    """Current status of the agent."""
    name: str
    state: AgentState
    uptime_seconds: float = 0.0
    total_trades: int = 0
    total_pnl_usd: float = 0.0
    win_rate: float = 0.0
    active_positions: int = 0
    last_signal: Optional[str] = None
    last_trade: Optional[str] = None
    errors_count: int = 0


class AgentMemory(BaseModel):
    """Agent's memory of past decisions and outcomes."""
    decision_id: str
    timestamp: datetime
    signal: CombinedSignal
    action_taken: StrategyAction
    outcome: Optional[str] = None  # "profit" | "loss" | "neutral"
    pnl_usd: Optional[float] = None
    lessons: list[str] = []
