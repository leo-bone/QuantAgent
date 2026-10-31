"""ERC-20 授权额度的单元测试（不触网、不依赖 web3 运行时）。

背景：此前 `_ensure_approval` 固定用 type(uint256).max 授权，一旦 router 合约
被攻破或被替换，钱包内该代币可被一次性全部划走。这里锁死新的分档行为与
「未知模式必须报错而不是降级」的约束。
"""

import pytest

from quantagent.execution.bsc_executor import (
    MAX_UINT256,
    resolve_approval_amount,
)

WEI = 10**18  # 1 token（18 位精度）


class TestExactMode:
    """默认档：只授权本笔交易所需。"""

    def test_returns_exactly_the_needed_amount(self):
        assert resolve_approval_amount(WEI, "exact") == WEI

    def test_is_the_default_mode(self):
        # 防回归：默认绝不能变成 unlimited
        assert resolve_approval_amount(WEI) == WEI

    def test_never_returns_max_uint(self):
        assert resolve_approval_amount(5 * WEI, "exact") != MAX_UINT256


class TestCappedMode:
    def test_multiplies_by_cap_multiple(self):
        assert resolve_approval_amount(WEI, "capped", 2.0) == 2 * WEI

    def test_fractional_multiple_rounds_down_but_keeps_minimum(self):
        got = resolve_approval_amount(WEI, "capped", 1.5)
        assert got == int(WEI * 1.5)
        assert got >= WEI

    def test_multiple_below_one_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_approval_amount(WEI, "capped", 0.5)


class TestUnlimitedMode:
    def test_returns_max_uint(self):
        assert resolve_approval_amount(WEI, "unlimited") == MAX_UINT256


class TestFailClosed:
    @pytest.mark.parametrize("bad", ["max", "", "EXACT ", None, "unlimted"])
    def test_unknown_mode_raises_instead_of_falling_back(self, bad):
        with pytest.raises(ValueError):
            resolve_approval_amount(WEI, bad)

    @pytest.mark.parametrize("bad_amount", [0, -1, -WEI, 1.5, "1000", True, None])
    def test_invalid_amount_rejected(self, bad_amount):
        with pytest.raises(ValueError):
            resolve_approval_amount(bad_amount, "exact")
