"""Risk manager - position sizing, drawdown limits, and safety checks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from quantagent.common.models import (
    Chain,
    CombinedSignal,
    RiskAssessment,
)


class RiskManager:
    """Manages risk across all trading operations.

    Responsibilities:
    - Position sizing based on portfolio and risk parameters
    - Maximum drawdown enforcement
    - Daily loss limits
    - Liquidity assessment
    - Emergency stop functionality
    """

    def __init__(
        self,
        max_position_size_usd: float = 1000.0,
        max_daily_loss_usd: float = 500.0,
        max_drawdown_pct: float = 0.15,
        max_slippage_bps: int = 100,
        min_liquidity_usd: float = 100000,
    ):
        self.max_position_size_usd = max_position_size_usd
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.max_slippage_bps = max_slippage_bps
        self.min_liquidity_usd = min_liquidity_usd

        self._daily_pnl: float = 0.0
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self._emergency_stop: bool = False
        self._daily_reset_time: datetime = datetime.now(timezone.utc)

    def assess_risk(
        self,
        signal: CombinedSignal,
        portfolio_usd: float = 0.0,
        volatility_24h: float = 0.0,
        liquidity_usd: float = 0.0,
    ) -> RiskAssessment:
        """Assess risk for a potential trade based on the signal.

        Returns a RiskAssessment with:
        - risk_score: 0 (safe) to 1 (dangerous)
        - max_position_usd: maximum position size
        - suggested_stop_loss_pct: recommended stop loss percentage
        - volatility_24h: 24h volatility
        - liquidity_score: 0 to 1
        - warnings: list of risk warnings
        """
        warnings = []
        risk_factors = 0.0

        # 1. Check emergency stop
        if self._emergency_stop:
            warnings.append("Emergency stop is active - no trading allowed")
            return RiskAssessment(
                risk_score=1.0,
                max_position_usd=0,
                suggested_stop_loss_pct=0.05,
                volatility_24h=volatility_24h,
                liquidity_score=0.0,
                warnings=warnings,
            )

        # 2. Daily loss check
        self._check_daily_reset()
        if self._daily_pnl < -self.max_daily_loss_usd:
            warnings.append(f"Daily loss limit reached: ${self._daily_pnl:.2f}")
            risk_factors += 0.4

        # 3. Drawdown check
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity
            if drawdown > self.max_drawdown_pct:
                warnings.append(f"Max drawdown exceeded: {drawdown:.1%}")
                risk_factors += 0.4

        # 4. Volatility risk
        if volatility_24h > 0.10:  # >10% daily volatility
            risk_factors += min(volatility_24h / 0.5, 0.3)
            warnings.append(f"High volatility: {volatility_24h:.1%}")

        # 5. Liquidity risk
        liquidity_score = min(liquidity_usd / self.min_liquidity_usd, 1.0) if liquidity_usd > 0 else 0.5
        if liquidity_usd < self.min_liquidity_usd:
            warnings.append(f"Low liquidity: ${liquidity_usd:,.0f}")
            risk_factors += 0.2

        # 6. Signal confidence risk (low confidence = higher risk)
        if signal.confidence < 0.5:
            risk_factors += 0.15
            warnings.append(f"Low signal confidence: {signal.confidence:.2f}")

        # Calculate position size
        # Scale down with risk factors
        risk_multiplier = max(1.0 - risk_factors, 0.1)
        max_position = self.max_position_size_usd * risk_multiplier

        # Scale with portfolio size (use max 10% of portfolio per trade)
        if portfolio_usd > 0:
            portfolio_limit = portfolio_usd * 0.10
            max_position = min(max_position, portfolio_limit)

        # Stop loss based on volatility
        suggested_stop = max(volatility_24h * 1.5, 0.03) if volatility_24h > 0 else 0.05

        risk_score = min(risk_factors, 1.0)

        return RiskAssessment(
            risk_score=risk_score,
            max_position_usd=max_position,
            suggested_stop_loss_pct=suggested_stop,
            volatility_24h=volatility_24h,
            liquidity_score=liquidity_score,
            warnings=warnings,
        )

    def record_pnl(self, pnl_usd: float) -> None:
        """Record a P&L result."""
        self._daily_pnl += pnl_usd
        self._current_equity += pnl_usd
        self._peak_equity = max(self._peak_equity, self._current_equity)

        if pnl_usd < 0:
            logger.warning(f"Loss recorded: ${pnl_usd:.2f} | Daily P&L: ${self._daily_pnl:.2f}")

        # Auto emergency stop if daily loss exceeded
        if self._daily_pnl < -self.max_daily_loss_usd * 1.5:
            self._emergency_stop = True
            logger.critical(f"Emergency stop triggered! Daily P&L: ${self._daily_pnl:.2f}")

    def set_emergency_stop(self, active: bool) -> None:
        """Manually set emergency stop."""
        self._emergency_stop = active
        logger.info(f"Emergency stop {'activated' if active else 'deactivated'}")

    @property
    def is_trading_allowed(self) -> bool:
        """Check if trading is currently allowed."""
        self._check_daily_reset()
        return not self._emergency_stop and self._daily_pnl > -self.max_daily_loss_usd

    def _check_daily_reset(self) -> None:
        """Reset daily P&L at UTC midnight."""
        now = datetime.now(timezone.utc)
        if now.date() > self._daily_reset_time.date():
            logger.info(f"Daily P&L reset. Previous: ${self._daily_pnl:.2f}")
            self._daily_pnl = 0.0
            self._daily_reset_time = now
