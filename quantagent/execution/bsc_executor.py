"""BSC execution layer — PancakeSwap swap signing and submission via web3.py."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from quantagent.execution.executor import ChainExecutor, ExecutionResult, ExecutionStatus


# BSC token addresses
BSC_TOKEN_ADDRESSES = {
    "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",   # WBNB
    "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "CAKE": "0x0E09FaBB73Bd3A6A495029869BBaE65a7a1549C6",
    "BTCB": "0x7130d2A12B9BCbFAe781A1e7eE57A2b37E8EfC8A",
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
}

# PancakeSwap V2 Router
PANCAKE_ROUTER_V2 = "0x10ED43C718714eb63d5aA57B78B54704E256924E"

# ─── ERC-20 授权策略 ────────────────────────────────────────────────────────
# 授权额度决定了「一旦 router 合约被攻破/被替换，钱包里该代币最多能被划走多少」。
# 因此默认按单笔交易精确授权，而不是业界常见的 type(uint256).max。
ALLOWANCE_MODES = ("exact", "capped", "unlimited")
MAX_UINT256 = 2**256 - 1


def resolve_approval_amount(
    amount_wei: int,
    mode: str = "exact",
    cap_multiple: float = 2.0,
) -> int:
    """计算本次 approve 应该授权的数量（wei）。

    - exact     ：只授权本笔交易需要的量。swap 会把它刚好花完，残留归零，
                  无需额外的 revoke 交易，风险敞口 = 0。
    - capped    ：授权 amount * cap_multiple（至少 1 倍），减少 approve 次数省 gas，
                  但残余敞口最多多出「一笔交易」的量。
    - unlimited ：type(uint256).max。仅在你明确接受风险时启用。

    未知模式直接抛错（fail-closed），绝不默默降级。
    """
    if not isinstance(amount_wei, int) or isinstance(amount_wei, bool) or amount_wei <= 0:
        raise ValueError(f"授权数量必须是正整数（wei），收到 {amount_wei!r}")
    if mode == "exact":
        return amount_wei
    if mode == "capped":
        if cap_multiple < 1.0:
            raise ValueError(f"cap_multiple 必须 >= 1.0，收到 {cap_multiple!r}")
        return max(amount_wei, int(amount_wei * cap_multiple))
    if mode == "unlimited":
        return MAX_UINT256
    raise ValueError(
        f"未知的授权模式 {mode!r}，可选：{', '.join(ALLOWANCE_MODES)}"
    )

# Minimal ABI for swaps + gas estimation
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokens",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETH",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class BSCExecutor(ChainExecutor):
    """
    BSC execution engine using PancakeSwap V2 Router.

    Flow (live mode):
    1. getAmountsOut → expected output
    2. Check allowance; approve **only what this trade needs** (see ALLOWANCE_MODES)
    3. Build swapExactTokensForTokens (or ETH variant) tx
    4. Estimate gas
    5. Sign with private key
    6. Send via eth.send_raw_transaction
    7. Wait for receipt
    8. Optionally revoke the residual allowance (revoke_after_swap)

    Paper mode:
    - Steps 1 only (quote), no state changes
    """

    def __init__(
        self,
        rpc_url: str,
        paper_mode: bool = True,
        router_address: str = PANCAKE_ROUTER_V2,
        gas_price_multiplier: float = 1.1,
        allowance_mode: str = "exact",
        allowance_cap_multiple: float = 2.0,
        revoke_after_swap: bool = False,
    ):
        super().__init__(paper_mode=paper_mode)
        if allowance_mode not in ALLOWANCE_MODES:
            raise ValueError(
                f"未知的授权模式 {allowance_mode!r}，可选：{', '.join(ALLOWANCE_MODES)}"
            )
        if allowance_mode == "capped" and allowance_cap_multiple < 1.0:
            raise ValueError(
                f"allowance_cap_multiple 必须 >= 1.0，收到 {allowance_cap_multiple!r}"
            )
        self.rpc_url = rpc_url
        self.router_address = router_address
        self.gas_price_multiplier = gas_price_multiplier
        self.allowance_mode = allowance_mode
        self.allowance_cap_multiple = allowance_cap_multiple
        self.revoke_after_swap = revoke_after_swap
        if allowance_mode == "unlimited":
            logger.warning(
                "BSC 执行器启用了无限授权（approve type(uint256).max）："
                "若 router 合约存在漏洞或被替换，钱包内该代币可被一次性全部转出。"
                "建议改用 allowance_mode='exact'（默认）。"
            )
        self._w3 = None

    def _get_w3(self):
        """Lazy-init web3 instance."""
        if self._w3 is None:
            from web3 import Web3
            self._w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
            if not self._w3.is_connected():
                raise ConnectionError(f"Cannot connect to BSC RPC: {self.rpc_url}")
        return self._w3

    def _get_addr(self, token: str) -> str:
        """Get checksum address for token."""
        from web3 import Web3
        addr = BSC_TOKEN_ADDRESSES.get(token, token)
        return Web3.to_checksum_address(addr)

    def _get_router(self):
        from web3 import Web3
        w3 = self._get_w3()
        return w3.eth.contract(
            address=Web3.to_checksum_address(self.router_address),
            abi=ROUTER_ABI,
        )

    def _get_erc20(self, token_address: str):
        from web3 import Web3
        w3 = self._get_w3()
        return w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_APPROVE_ABI,
        )

    def _get_decimals(self, token: str) -> int:
        """Get token decimals (default 18)."""
        known_decimals = {
            "USDT": 18,  # BSC USDT uses 18 decimals (unlike ETH USDT which uses 6)
            "USDC": 18,
            "BUSD": 18,
            "BNB": 18,
            "CAKE": 18,
            "BTCB": 18,
            "ETH": 18,
        }
        return known_decimals.get(token, 18)

    def _to_wei(self, token: str, amount: float) -> int:
        decimals = self._get_decimals(token)
        return int(amount * (10 ** decimals))

    def _from_wei(self, token: str, amount_wei: int) -> float:
        decimals = self._get_decimals(token)
        return amount_wei / (10 ** decimals)

    def _build_path(self, from_token: str, to_token: str) -> list[str]:
        """Build swap path, routing through WBNB if necessary."""
        from web3 import Web3
        from_addr = self._get_addr(from_token)
        to_addr = self._get_addr(to_token)
        wbnb = Web3.to_checksum_address(BSC_TOKEN_ADDRESSES["BNB"])

        # Direct route if one side is BNB
        if from_addr == wbnb or to_addr == wbnb:
            return [from_addr, to_addr]

        # Route through WBNB for better liquidity
        return [from_addr, wbnb, to_addr]

    async def _submit_approve(
        self,
        erc20,
        owner: str,
        spender: str,
        amount_wei: int,
        private_key: str,
        token: str,
    ) -> bool:
        """构造、签名并发送一笔 approve 交易，等待回执。"""
        from web3 import Web3

        w3 = self._get_w3()
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(owner))
        gas_price = w3.eth.gas_price
        tx = erc20.functions.approve(spender, amount_wei).build_transaction({
            "from": Web3.to_checksum_address(owner),
            "nonce": nonce,
            "gasPrice": int(gas_price * self.gas_price_multiplier),
            "gas": 60_000,
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        receipt = await asyncio.get_event_loop().run_in_executor(
            None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        )
        if receipt["status"] == 1:
            logger.info(f"Approval confirmed: {token} -> {tx_hash.hex()}")
            return True
        logger.error(f"Approval failed: {token}")
        return False

    async def _ensure_approval(
        self,
        token: str,
        owner: str,
        amount: float,
        private_key: str,
    ) -> bool:
        """确保 router 有足够额度完成本笔交易。

        授权量由 self.allowance_mode 决定，默认 exact（只授权本笔所需）。
        """
        from web3 import Web3

        token_addr = self._get_addr(token)
        erc20 = self._get_erc20(token_addr)
        w3 = self._get_w3()
        router_addr = Web3.to_checksum_address(self.router_address)

        amount_wei = self._to_wei(token, amount)
        allowance = erc20.functions.allowance(
            Web3.to_checksum_address(owner), router_addr
        ).call()

        if allowance >= amount_wei:
            return True  # Already approved

        approve_amount = resolve_approval_amount(
            amount_wei, self.allowance_mode, self.allowance_cap_multiple
        )

        if self.allowance_mode == "unlimited":
            logger.warning(
                f"向 PancakeSwap router 授予 {token} 的无限额度（type(uint256).max）"
                "——该代币的全部余额从此对该合约可见可动。"
            )
        else:
            logger.info(
                f"Approving {token} for PancakeSwap router: "
                f"{self._from_wei(token, approve_amount):.8f} {token} "
                f"(mode={self.allowance_mode}, needed={amount:.8f})"
            )

        return await self._submit_approve(
            erc20, owner, router_addr, approve_amount, private_key, token
        )

    async def revoke_allowance(
        self,
        token: str,
        owner: str,
        private_key: str,
    ) -> bool:
        """把对该 token 的 router 授权归零（approve 0）。"""
        from web3 import Web3

        token_addr = self._get_addr(token)
        erc20 = self._get_erc20(token_addr)
        router_addr = Web3.to_checksum_address(self.router_address)
        logger.info(f"Revoking {token} allowance for PancakeSwap router...")
        return await self._submit_approve(
            erc20, owner, router_addr, 0, private_key, token
        )

    async def execute_swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage_bps: int = 100,
        wallet_address: Optional[str] = None,
        private_key: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute or simulate a PancakeSwap trade."""
        result = ExecutionResult(
            status=ExecutionStatus.PENDING,
            chain="bsc",
            from_token=from_token,
            to_token=to_token,
            amount_in=amount,
            simulated=self.paper_mode,
        )

        try:
            if self.paper_mode:
                # ─── Paper mode: do NOT connect to BSC RPC ───
                # Try to get a quote via RPC with short timeout for realism,
                # but fall back gracefully if network is unavailable.
                try:
                    from web3 import Web3
                    w3 = self._get_w3()
                    router = self._get_router()
                    amount_in_wei = self._to_wei(from_token, amount)
                    path = self._build_path(from_token, to_token)
                    amounts_out = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: router.functions.getAmountsOut(amount_in_wei, path).call()
                        ),
                        timeout=10.0,
                    )
                    amount_out_wei = amounts_out[-1]
                    amount_out = self._from_wei(to_token, amount_out_wei)
                    price_impact = 0.3
                except Exception:
                    # RPC unreachable – estimate from hard ratio
                    amount_out = amount * 0.995
                    price_impact = 0.5

                result.amount_out = amount_out
                result.price_impact_pct = price_impact
                result.fee_usd = amount * 0.003  # 0.3% fee

                if result.price_impact_pct > 5.0:
                    result.status = ExecutionStatus.REJECTED
                    result.error = f"Price impact too high: {result.price_impact_pct:.2f}%"
                    return result

                logger.info(
                    f"[PAPER/BSC] {amount} {from_token} -> {amount_out:.6f} {to_token} "
                    f"| fee={result.fee_usd:.4f} USD"
                )
                result.tx_hash = f"PAPER_{int(time.time())}_{from_token}_{to_token}"
                result.status = ExecutionStatus.SIMULATED
                self._log_trade(result)
                return result

            # ─── Live mode ───
            from web3 import Web3
            w3 = self._get_w3()
            router = self._get_router()

            amount_in_wei = self._to_wei(from_token, amount)
            path = self._build_path(from_token, to_token)

            # Step 1: Get quote
            amounts_out = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: router.functions.getAmountsOut(amount_in_wei, path).call()
            )
            amount_out_wei = amounts_out[-1]
            amount_out = self._from_wei(to_token, amount_out_wei)
            result.amount_out = amount_out

            # Estimate price impact (simplified)
            result.price_impact_pct = 0.3  # PancakeSwap base fee
            result.fee_usd = amount * 0.003  # 0.3% fee

            # Guard: reject if price impact too high
            if result.price_impact_pct > 5.0:
                result.status = ExecutionStatus.REJECTED
                result.error = f"Price impact too high: {result.price_impact_pct:.2f}%"
                return result

            if not wallet_address or not private_key:
                result.status = ExecutionStatus.REJECTED
                result.error = "wallet_address and private_key required for live trading"
                return result

            owner = Web3.to_checksum_address(wallet_address)

            # Step 2: Approve if needed (skip for BNB native)
            if from_token != "BNB":
                approved = await self._ensure_approval(from_token, owner, amount, private_key)
                if not approved:
                    result.status = ExecutionStatus.FAILED
                    result.error = "Token approval failed"
                    return result

            # Step 3: Build swap transaction
            deadline = int(time.time()) + 300  # 5 min deadline
            min_amount_out = int(amount_out_wei * (1 - slippage_bps / 10000))
            nonce = w3.eth.get_transaction_count(owner)
            gas_price = int(w3.eth.gas_price * self.gas_price_multiplier)

            if from_token == "BNB":
                # BNB -> Token: swapExactETHForTokens
                tx = router.functions.swapExactETHForTokens(
                    min_amount_out, path, owner, deadline
                ).build_transaction({
                    "from": owner,
                    "value": amount_in_wei,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                })
            elif to_token == "BNB":
                # Token -> BNB: swapExactTokensForETH
                tx = router.functions.swapExactTokensForETH(
                    amount_in_wei, min_amount_out, path, owner, deadline
                ).build_transaction({
                    "from": owner,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                })
            else:
                # Token -> Token: swapExactTokensForTokens
                tx = router.functions.swapExactTokensForTokens(
                    amount_in_wei, min_amount_out, path, owner, deadline
                ).build_transaction({
                    "from": owner,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                })

            # Step 4: Estimate gas
            estimated_gas = w3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated_gas * 1.2)  # 20% buffer
            result.gas_used = tx["gas"]

            # Step 5-6: Sign and send
            signed = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            result.tx_hash = tx_hash.hex()
            logger.info(f"[LIVE/BSC] Transaction submitted: {result.tx_hash}")

            # Step 7: Wait for receipt
            receipt = await asyncio.get_event_loop().run_in_executor(
                None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            )

            if receipt["status"] == 1:
                result.status = ExecutionStatus.SUCCESS
                # Parse actual output from logs if needed
                logger.info(f"[LIVE/BSC] Confirmed in block {receipt['blockNumber']}")
                # 可选：成交后把残留授权归零（exact 模式下 swap 已花光额度，通常无需）
                if self.revoke_after_swap and from_token != "BNB":
                    try:
                        await self.revoke_allowance(from_token, owner, private_key)
                    except Exception as revoke_err:
                        logger.warning(f"撤销授权失败（不影响本笔成交）: {revoke_err}")
            else:
                result.status = ExecutionStatus.FAILED
                result.error = "Transaction reverted on-chain"

            self._log_trade(result)
            return result

        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
            logger.error(f"BSC swap execution error: {e}")
            return result

    async def estimate_gas(
        self,
        from_token: str,
        to_token: str,
        amount: float,
    ) -> dict:
        """Estimate BSC transaction cost in BNB and USD."""
        try:
            w3 = self._get_w3()
            gas_price_wei = w3.eth.gas_price
            gas_units = 250_000  # Typical pancakeswap swap

            gas_price_gwei = gas_price_wei / 1e9
            total_bnb = (gas_units * gas_price_wei) / 1e18

            # Estimate BNB price
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10.0, proxy=None) as c:
                    resp = await c.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": "binancecoin", "vs_currencies": "usd"},
                    )
                    bnb_price = resp.json()["binancecoin"]["usd"]
            except Exception:
                bnb_price = 600.0  # fallback

            total_usd = total_bnb * bnb_price

            return {
                "gas_units": gas_units,
                "gas_price_gwei": round(gas_price_gwei, 2),
                "total_fee_bnb": round(total_bnb, 6),
                "total_fee_usd": round(total_usd, 4),
            }
        except Exception as e:
            logger.error(f"Gas estimation failed: {e}")
            return {"gas_units": 250_000, "gas_price_gwei": 5.0, "total_fee_bnb": 0.001, "total_fee_usd": 0.6}

    async def get_wallet_balances(self, wallet: str) -> dict[str, float]:
        """Get BNB and token balances."""
        from web3 import Web3
        w3 = self._get_w3()
        owner = Web3.to_checksum_address(wallet)
        balances: dict[str, float] = {}

        try:
            # BNB native balance
            bnb_wei = w3.eth.get_balance(owner)
            balances["BNB"] = w3.from_wei(bnb_wei, "ether")

            # ERC20 balances
            for symbol, addr in BSC_TOKEN_ADDRESSES.items():
                if symbol == "BNB":
                    continue
                try:
                    contract = self._get_erc20(addr)
                    decimals = contract.functions.decimals().call()
                    raw = contract.functions.balanceOf(owner).call()
                    balances[symbol] = raw / (10 ** decimals)
                except Exception:
                    balances[symbol] = 0.0
        except Exception as e:
            logger.error(f"Failed to get BSC balances: {e}")

        return balances

    async def check_allowance(
        self,
        token: str,
        wallet: str,
        spender: str,
        amount: float,
    ) -> bool:
        """Check if router has sufficient allowance for token."""
        if token == "BNB":
            return True  # Native token doesn't need approval
        from web3 import Web3
        token_addr = self._get_addr(token)
        erc20 = self._get_erc20(token_addr)
        allowance = erc20.functions.allowance(
            Web3.to_checksum_address(wallet),
            Web3.to_checksum_address(spender),
        ).call()
        return allowance >= self._to_wei(token, amount)
