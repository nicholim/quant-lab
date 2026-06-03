"""Pluggable commission and slippage models.

A small library mirroring the common cases backtrader exposes via
``CommInfoBase`` (percentage / fixed-per-trade / per-share) and its pluggable
slippage. The models are injected into :class:`~src.execution.SimulatedExecution`
at its single fill/slippage point, so the execution engine stays agnostic to the
cost policy.

Back-compat: ``SimulatedExecution`` still accepts the original
``commission_pct``/``slippage_pct`` floats; when no model is injected it builds
:class:`PercentCommission`/:class:`PercentSlippage` from those floats so fills are
byte-identical to the prior behavior.

NOTE: these model *explicit* transaction costs only. There is no borrow-fee /
locate / hard-to-borrow model, so short P&L net of these costs is still an
idealized mechanics demonstration, not a realistic short-financing model.
"""

from abc import ABC, abstractmethod

from .events import Direction


class CommissionModel(ABC):
    """Maps an executed (price, qty) to a commission charge in cash terms."""

    @abstractmethod
    def commission(self, price: float, qty: int) -> float: ...


class PercentCommission(CommissionModel):
    """A fixed fraction of traded notional (``price * qty``).

    Wraps the framework's original commission behavior; this is the default so
    omitting a model leaves fills unchanged.
    """

    def __init__(self, pct: float = 0.001):
        self.pct = pct

    def commission(self, price: float, qty: int) -> float:
        return price * qty * self.pct


class PerShareCommission(CommissionModel):
    """A flat fee per share/contract, with an optional per-trade minimum floor.

    Models the Interactive-Brokers-style schedule backtrader exposes as a
    per-share ``CommInfoBase`` (e.g. ``0.005`` per share, ``minimum=1.0``).
    """

    def __init__(self, per_share: float, minimum: float = 0.0):
        self.per_share = per_share
        self.minimum = minimum

    def commission(self, price: float, qty: int) -> float:
        return max(self.per_share * qty, self.minimum)


class FixedCommission(CommissionModel):
    """A flat fee per trade, independent of price or quantity."""

    def __init__(self, fee: float):
        self.fee = fee

    def commission(self, price: float, qty: int) -> float:
        return self.fee


class SlippageModel(ABC):
    """Adjusts an intended fill price for the direction being executed.

    BUYs slip up (you pay more), SELLs slip down (you receive less).
    """

    @abstractmethod
    def adjust(self, price: float, direction: Direction) -> float: ...


class PercentSlippage(SlippageModel):
    """Symmetric percentage slippage (the framework's original model)."""

    def __init__(self, pct: float = 0.0005):
        self.pct = pct

    def adjust(self, price: float, direction: Direction) -> float:
        if direction == Direction.BUY:
            return price * (1 + self.pct)
        return price * (1 - self.pct)


class FixedBpsSlippage(SlippageModel):
    """Symmetric slippage expressed in basis points (1 bp = 0.0001)."""

    def __init__(self, bps: float):
        self.bps = bps

    def adjust(self, price: float, direction: Direction) -> float:
        frac = self.bps / 10_000.0
        if direction == Direction.BUY:
            return price * (1 + frac)
        return price * (1 - frac)
