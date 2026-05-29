"""Abstract base for chain execution layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    SIMULATED = "simulated"     # Paper trade result
    REJECTED = "rejected"       # Pre-flight check failed


@dataclass
class ExecutionResult:
    """Unified result from any chain execution."""
    status: ExecutionStatus
    tx_hash: str = ""
    chain: str = ""
    from_token: str = ""
    to_token: str = ""
    amount_in: float = 0.0
    amount_out: float = 0.0
    price_impact_pct: float = 0.0
    fee_usd: float = 0.0
    gas_used: int = 0
    error: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    simulated: bool = False     # True if this is a paper trade

    @property
    def effective_price(self) -> float:
        """Price per unit of output token."""
        if self.amount_out == 0:
            return 0.0
        return self.amount_in / self.amount_out

    @property
    def is_success(self) -> bool:
        return self.status in (ExecutionStatus.SUCCESS, ExecutionStatus.SIMULATED)


class ChainExecutor(ABC):
    """Abstract base for chain-specific execution engines."""

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode

    @abstractmethod
    async def execute_swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int = 100,
        wallet_address: Optional[str] = None,
        private_key: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute or simulate a token swap.

        Args:
            from_token: Source token symbol or address
            to_token: Destination token symbol or address
            amount: Amount in source token units
            slippage_bps: Max slippage in basis points (100 = 1%)
            wallet_address: Wallet to trade from (required for live)
            private_key: Private key for signing (required for live, NEVER log this)

        Returns:
            ExecutionResult with tx hash and amounts
        """
        ...

    @abstractmethod
    async def estimate_gas(
        self,
        from_token: str,
        to_token: str,
        amount: float,
    ) -> dict:
        """Estimate transaction cost before execution.

        Returns dict with:
            - gas_units: estimated gas
            - gas_price_gwei: current gas price
            - total_fee_usd: estimated total fee in USD
        """
        ...

    @abstractmethod
    async def get_wallet_balances(self, wallet: str) -> dict[str, float]:
        """Get all relevant token balances for a wallet."""
        ...

    @abstractmethod
    async def check_allowance(
        self,
        token: str,
        wallet: str,
        spender: str,
        amount: float,
    ) -> bool:
        """Check if spender has sufficient allowance (EVM chains)."""
        ...

    def _log_trade(self, result: ExecutionResult):
        """Log execution result (never log private keys)."""
        from loguru import logger
        prefix = "[PAPER]" if result.simulated else "[LIVE]"
        if result.is_success:
            logger.info(
                f"{prefix} Swap {result.amount_in:.6f} {result.from_token} "
                f"-> {result.amount_out:.6f} {result.to_token} | "
                f"price_impact={result.price_impact_pct:.2f}% | "
                f"fee=${result.fee_usd:.4f} | "
                f"tx={result.tx_hash[:16]}..." if result.tx_hash else ""
            )
        else:
            logger.error(f"{prefix} Swap FAILED: {result.error}")
