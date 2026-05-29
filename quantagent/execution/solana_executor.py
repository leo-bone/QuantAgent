"""Solana execution layer — Jupiter API swap signing and submission."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Optional

import httpx
from loguru import logger

from quantagent.execution.executor import ChainExecutor, ExecutionResult, ExecutionStatus

# Well-known token mints (Mainnet-beta verified via Solscan/Solana Explorer)
SOL_TOKEN_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
}

# Token decimals
SOL_TOKEN_DECIMALS = {
    "SOL": 9,
    "USDC": 6,
    "USDT": 6,
    "BONK": 5,
    "JUP": 6,
    "WIF": 6,
}


class SolanaExecutor(ChainExecutor):
    """
    Solana execution engine using Jupiter Aggregator.

    Flow (live mode):
    1. GET /quote  -> route plan
    2. POST /swap  -> serialized transaction
    3. Sign transaction with private key (via solders/solana-py)
    4. Send to RPC via sendTransaction
    5. Poll confirmTransaction until finalized

    Paper mode:
    - Calls steps 1-2 but does NOT sign or send
    - Returns SIMULATED result with expected output amounts
    """

    JUPITER_API = "https://quote-api.jup.ag/v6"

    def __init__(
        self,
        rpc_url: str,
        paper_mode: bool = True,
        priority_fee_lamports: int = 10_000,
    ):
        super().__init__(paper_mode=paper_mode)
        self.rpc_url = rpc_url
        self.priority_fee_lamports = priority_fee_lamports
        self._client = httpx.AsyncClient(timeout=30.0, proxy=None)

    def _to_lamports(self, token: str, amount: float) -> int:
        """Convert human-readable amount to smallest unit."""
        decimals = SOL_TOKEN_DECIMALS.get(token, 9)
        return int(amount * (10 ** decimals))

    def _from_lamports(self, token: str, lamports: int) -> float:
        """Convert smallest unit to human-readable amount."""
        decimals = SOL_TOKEN_DECIMALS.get(token, 9)
        return lamports / (10 ** decimals)

    def _get_mint(self, token: str) -> str:
        """Resolve token symbol to mint address."""
        return SOL_TOKEN_MINTS.get(token, token)

    async def _get_quote(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int,
    ) -> dict:
        """Fetch a swap quote from Jupiter."""
        from_mint = self._get_mint(from_token)
        to_mint = self._get_mint(to_token)
        amount_lamports = self._to_lamports(from_token, amount)

        resp = await self._client.get(
            f"{self.JUPITER_API}/quote",
            params={
                "inputMint": from_mint,
                "outputMint": to_mint,
                "amount": amount_lamports,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": "false",
                "asLegacyTransaction": "false",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_swap_transaction(
        self,
        quote: dict,
        wallet_address: str,
    ) -> str:
        """Get serialized swap transaction from Jupiter."""
        payload = {
            "quoteResponse": quote,
            "userPublicKey": wallet_address,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": self.priority_fee_lamports,
        }
        resp = await self._client.post(
            f"{self.JUPITER_API}/swap",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["swapTransaction"]  # base64 encoded versioned transaction

    async def _sign_and_send(
        self,
        serialized_tx_b64: str,
        private_key: str,
    ) -> str:
        """Sign and submit transaction. Returns tx signature (hash)."""
        try:
            # Import solders for Solana transaction signing
            from solders.keypair import Keypair  # type: ignore
            from solders.transaction import VersionedTransaction  # type: ignore
        except ImportError:
            logger.error("solders package required for live trading: pip install solders")
            raise RuntimeError("solders not installed")

        # Decode base64 transaction
        tx_bytes = base64.b64decode(serialized_tx_b64)

        # Parse versioned transaction
        tx = VersionedTransaction.from_bytes(tx_bytes)

        # Load keypair from private key (base58 string)
        # SECURITY: private_key should come from environment, never from logs
        try:
            import base58  # type: ignore
            secret = base58.b58decode(private_key)
        except ImportError:
            # Try raw bytes if base58 not available
            secret = bytes.fromhex(private_key) if len(private_key) == 128 else private_key.encode()

        keypair = Keypair.from_bytes(secret[:64])

        # Sign the transaction
        tx.sign([keypair])

        # Serialize signed transaction
        signed_bytes = bytes(tx)
        signed_b64 = base64.b64encode(signed_bytes).decode()

        # Send to RPC
        async with httpx.AsyncClient(timeout=60.0, proxy=None) as rpc_client:
            resp = await rpc_client.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        signed_b64,
                        {
                            "encoding": "base64",
                            "skipPreflight": False,
                            "preflightCommitment": "processed",
                        },
                    ],
                },
            )
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                raise RuntimeError(f"RPC error: {result['error']}")
            return result["result"]  # transaction signature

    async def _wait_for_confirmation(
        self,
        tx_hash: str,
        max_retries: int = 30,
        poll_interval: float = 1.0,
    ) -> bool:
        """Poll for transaction confirmation."""
        for _ in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0, proxy=None) as c:
                    resp = await c.post(
                        self.rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignatureStatuses",
                            "params": [[tx_hash]],
                        },
                    )
                    data = resp.json()
                    status = data["result"]["value"][0]
                    if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
                        return True
                    if status and status.get("err"):
                        logger.error(f"Transaction failed on-chain: {status['err']}")
                        return False
            except Exception as e:
                logger.warning(f"Polling error: {e}")

            import asyncio
            await asyncio.sleep(poll_interval)

        logger.warning(f"Transaction {tx_hash} not confirmed after {max_retries} retries")
        return False

    async def execute_swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int = 100,
        wallet_address: Optional[str] = None,
        private_key: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute or simulate a Jupiter swap."""
        result = ExecutionResult(
            status=ExecutionStatus.PENDING,
            chain="solana",
            from_token=from_token,
            to_token=to_token,
            amount_in=amount,
            simulated=self.paper_mode,
        )

        try:
            if self.paper_mode:
                # ─── Paper mode: do NOT call external APIs ───
                # Try to get a quote with short timeout for realism, but fall
                # back gracefully if network/Jupiter is unavailable.
                try:
                    quote = await asyncio.wait_for(
                        self._get_quote(from_token, to_token, amount, slippage_bps),
                        timeout=10.0,
                    )
                    amount_out = self._from_lamports(to_token, int(quote.get("outAmount", 0)))
                    price_impact = float(quote.get("priceImpactPct", 0))
                except Exception:
                    # Network down / Jupiter unavailable – estimate from hard ratio
                    amount_out = amount * 0.995  # rough 0.5% fee assumption
                    price_impact = 0.5

                result.amount_out = amount_out
                result.price_impact_pct = price_impact
                result.fee_usd = 0.001  # ~0.001 SOL gas

                if price_impact > 5.0:
                    result.status = ExecutionStatus.REJECTED
                    result.error = f"Price impact too high: {price_impact:.2f}%"
                    self._log_trade(result)
                    return result

                logger.info(
                    f"[PAPER/SOL] {amount} {from_token} -> {amount_out:.6f} {to_token} "
                    f"| impact={price_impact:.3f}%"
                )
                result.tx_hash = f"PAPER_{int(time.time())}_{from_token}_{to_token}"
                result.status = ExecutionStatus.SIMULATED
                self._log_trade(result)
                return result

            # ─── Live mode ───
            # Step 1: Get quote
            quote = await self._get_quote(from_token, to_token, amount, slippage_bps)
            amount_out = self._from_lamports(to_token, int(quote.get("outAmount", 0)))
            price_impact = float(quote.get("priceImpactPct", 0))
            result.amount_out = amount_out
            result.price_impact_pct = price_impact
            result.fee_usd = 0.001  # ~0.001 SOL gas

            # Guard: reject if price impact too high
            if price_impact > 5.0:
                result.status = ExecutionStatus.REJECTED
                result.error = f"Price impact too high: {price_impact:.2f}%"
                self._log_trade(result)
                return result

            if not wallet_address or not private_key:
                result.status = ExecutionStatus.REJECTED
                result.error = "wallet_address and private_key required for live trading"
                return result

            # Step 2: Get swap transaction
            serialized_tx = await self._get_swap_transaction(quote, wallet_address)

            # Step 3-4: Sign and send
            tx_hash = await self._sign_and_send(serialized_tx, private_key)
            result.tx_hash = tx_hash
            logger.info(f"[LIVE/SOL] Transaction submitted: {tx_hash}")

            # Step 5: Confirm
            confirmed = await self._wait_for_confirmation(tx_hash)
            result.status = ExecutionStatus.SUCCESS if confirmed else ExecutionStatus.FAILED
            if not confirmed:
                result.error = "Transaction not confirmed within timeout"

            self._log_trade(result)
            return result

        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
            logger.error(f"Solana swap execution error: {e}")
            return result

    async def estimate_gas(
        self,
        from_token: str,
        to_token: str,
        amount: float,
    ) -> dict:
        """Estimate Solana transaction cost."""
        # Solana fees are ~0.000005 SOL base + priority fee
        # Get current SOL price for USD estimate
        try:
            from quantagent.data.solana_collector import SolanaProvider
            provider = SolanaProvider(self.rpc_url)
            sol_price = await provider.get_price("SOL")
            await provider.close()
        except Exception:
            sol_price = 150.0  # fallback

        base_fee = 0.000005  # 5000 lamports
        priority_fee = self.priority_fee_lamports / 1e9
        total_sol = base_fee + priority_fee
        total_usd = total_sol * sol_price

        return {
            "gas_units": 200_000,  # ~200k compute units typical
            "gas_price_gwei": 0,   # Not applicable for Solana
            "total_fee_sol": total_sol,
            "total_fee_usd": total_usd,
        }

    async def get_wallet_balances(self, wallet: str) -> dict[str, float]:
        """Get SOL and SPL token balances."""
        balances: dict[str, float] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0, proxy=None) as c:
                # SOL balance
                resp = await c.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getBalance",
                        "params": [wallet],
                    },
                )
                data = resp.json()
                sol_lamports = data.get("result", {}).get("value", 0)
                balances["SOL"] = sol_lamports / 1e9

                # SPL token accounts
                resp2 = await c.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            wallet,
                            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                            {"encoding": "jsonParsed"},
                        ],
                    },
                )
                data2 = resp2.json()
                for account in data2.get("result", {}).get("value", []):
                    info = account["account"]["data"]["parsed"]["info"]
                    mint = info.get("mint", "")
                    amount = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
                    # Reverse-lookup symbol
                    for sym, m in SOL_TOKEN_MINTS.items():
                        if m == mint:
                            balances[sym] = amount
                            break
        except Exception as e:
            logger.error(f"Failed to get Solana balances: {e}")

        return balances

    async def check_allowance(
        self,
        token: str,
        wallet: str,
        spender: str,
        amount: float,
    ) -> bool:
        """Not applicable for Solana (SPL uses different model)."""
        # Solana SPL token accounts use authority/delegation instead of allowances
        return True

    async def close(self):
        await self._client.aclose()
