# Avellaneda–Stoikov market making

> Implemented in `cpp/order-book/python/abides_lite.py` (`AvellanedaStoikovAgent`,
> `SimulationKernel`, `compare_market_makers`). Quotes are placed by the
> closed-form Avellaneda–Stoikov policy; **fills are decided by the real C++
> price-time-priority matching engine**, not by an assumed fill-intensity model.

This note explains the market maker's optimal-control problem, the Avellaneda &
Stoikov (2008) solution, exactly how the implementation realises it on a live
matching engine, and the honest limitations.

---

## 1. The market maker's problem

A market maker (MM) continuously posts a bid quote (a buy limit order below the
mid) and an ask quote (a sell limit order above the mid). When an incoming
*marketable* order hits one of them, the MM earns roughly half the spread. If
buy and sell flow were perfectly balanced the MM would simply collect the spread
forever.

The catch is **inventory risk**. Fills are random and one-sided in bursts: a run
of buyer-initiated trades leaves the MM *short*, a run of sellers leaves it
*long*. Any non-zero inventory $q$ is an unhedged directional position, so if the
mid-price $S$ then moves, the MM has mark-to-market P&L variance it never wanted.
The more inventory it carries, the more its terminal wealth depends on price
direction rather than on captured spread.

So the MM faces a tension:

- **Quote tight** (close to the mid) → high fill rate → lots of spread captured,
  but inventory swings violently.
- **Quote wide, and skew** (shift both quotes in the direction that offloads
  inventory) → controlled inventory, but less flow captured.

Avellaneda & Stoikov formalise this as a **stochastic optimal control** problem:
choose the bid/ask quote distances dynamically to maximise the expected utility
of terminal wealth (cash + inventory marked at the mid) at a horizon $T$, under
**CARA** (constant absolute risk aversion, exponential) utility with risk
aversion $\gamma$. The exponential utility is what makes inventory *risk* — not
just expected inventory — enter the objective.

---

## 2. The Avellaneda–Stoikov model (2008)

**Setup.** The mid-price follows an arithmetic Brownian motion

$$dS_t = \sigma\, dW_t,$$

with volatility $\sigma$. The MM posts a bid at distance $\delta^b$ and an ask at
distance $\delta^a$ from a reference price. Orders arrive as a Poisson process
whose intensity *decreases* with how far the quote sits from the mid:

$$\lambda(\delta) = A\, e^{-k\,\delta}.$$

The further out you quote, the rarer the fill — $k$ is the **order-flow decay /
liquidity** parameter ($A$ scales the base arrival rate). Solving the
Hamilton–Jacobi–Bellman equation for the CARA value function yields two
closed-form results (the practical approximation made famous by the paper).

### 2.1 Reservation (indifference) price

$$\boxed{\,r(s, q, t) = s - q\,\gamma\,\sigma^2\,(T - t)\,}$$

This is the price at which the MM is *indifferent* to holding its current
inventory $q$ — its private valuation of the asset given the risk it is carrying.
Read the terms:

- **It skews against inventory.** When the MM is **long** ($q > 0$) the
  reservation price is pulled *below* the mid; quoting around it means a lower,
  more aggressive ask and a lower, less aggressive bid — i.e. the MM leans on
  *selling* to flatten. When **short** ($q < 0$) it is pulled above the mid to
  lean on buying. This inventory skew is the entire point of the model.
- **The skew scales with $\gamma\sigma^2(T-t)$:** more risk aversion, more
  volatility, or more time left to the horizon all make the MM more eager to
  offload inventory now.
- **It vanishes at the horizon.** As $t \to T$, $(T-t) \to 0$ and the skew
  disappears: with no time left for price risk to bite, inventory stops mattering
  and the reservation price collapses back to the mid.

### 2.2 Optimal spread

$$\boxed{\,\delta^a + \delta^b = \gamma\,\sigma^2\,(T - t) + \frac{2}{\gamma}\ln\!\Big(1 + \frac{\gamma}{k}\Big)\,}$$

so the per-side **half-spread** is

$$\delta = \tfrac{1}{2}\,\gamma\,\sigma^2\,(T - t) + \frac{1}{\gamma}\ln\!\Big(1 + \frac{\gamma}{k}\Big).$$

Two economically distinct pieces:

1. **Inventory / risk term $\tfrac12\gamma\sigma^2(T-t)$** — widen the spread when
   risk aversion, volatility, or remaining horizon are large. This shrinks to
   zero at $T$.
2. **Microstructure / liquidity term $\frac{1}{\gamma}\ln(1+\gamma/k)$** — set by
   the order-flow decay $k$ and risk aversion $\gamma$. It is the floor the MM
   charges for immediacy regardless of inventory, and it is what remains as
   $t \to T$.

> **A transcription caveat.** Several secondary sources (and blog
> re-derivations) write the liquidity term as $\frac{2}{k}\ln(1+\gamma/k)$ or
> similar. The correct per-side limiting form from the original paper is
> $\frac{1}{\gamma}\ln(1+\gamma/k)$ — which is exactly what this repo implements
> (`math.log1p(gamma / k) / gamma`).

### 2.3 Quotes are placed around the *reservation* price

$$\text{bid} = r - \delta, \qquad \text{ask} = r + \delta.$$

The quotes straddle $r$, **not** the mid $S$. Because $r$ already carries the
inventory skew, a long MM's whole quote ladder slides down (its ask becomes
easier to hit, its bid harder), mechanically pushing inventory back toward zero.
A naive MM that quotes symmetrically around the *mid* has no such control and
lets inventory random-walk.

---

## 3. How this implementation works

The distinguishing feature versus model-based simulators (mbt-gym, or the
Avellaneda–Stoikov paper's own Monte Carlo) is that **fills are not assumed**.
There, whether a quote at distance $\delta$ fills is drawn from the intensity
$\lambda(\delta) = A e^{-k\delta}$. **Here, the agent quotes POST_ONLY limit
orders into a real C++ price-time-priority matching engine through the pybind11
binding, and whether they fill is decided by actual incoming order flow hitting
the book.** A-S is used only to *place* quotes; the matching engine adjudicates
execution. That makes it a more realistic test of the policy — the agent is
subject to real queue position and the actual sequence of arriving orders.

### 3.1 The discrete-event kernel

`SimulationKernel` is an ABIDES-style discrete-event simulator:

- A **min-heap of timestamped events** keyed by integer simulation time (FIFO
  tie-break by insertion order), processed in strict time order.
- Each agent has a one-way **`latency`**: a `WAKEUP` at time $t$ produces order
  actions that *arrive* at the book at $t + \text{latency}$. So the order in
  which the engine sees orders reflects latency, not decision order — the
  headline ABIDES capability.
- **Order arrivals** route the agent's action dicts into `OrderBook.add_order`.
- **Cancel events** route to `OrderBook.cancel_order` (also latency-delayed), so
  an agent's cancel can arrive *after* a fill that already consumed the order —
  exactly as in a real venue.
- **Fill attribution:** the kernel maintains an `order_id → (agent, side)` map.
  When a trade prints, it looks up the taker and the resting maker and calls each
  owning agent's `on_fill(...)`, updating that agent's inventory with the correct
  sign — **a BUY fill adds `+qty`, a SELL fill `−qty`**
  (`abides_lite.py:419`). The running tally is also exposed on
  `KernelResult.agent_inventory`.

This closes the control loop: real fills update `self.inventory`, and
`self.inventory` feeds straight back into the next `compute_quotes(q=...)` call,
so the skew responds to genuine execution.

### 3.2 The agent

`AvellanedaStoikovAgent.compute_quotes(mid, q, tau)` is a pure function returning
`(reservation, half_spread, bid, ask)` implementing §2 verbatim
(`abides_lite.py:349`):

```python
sigma = self._sigma()              # online stdev of observed mids, floored
var = sigma * sigma
reservation = mid - q * self.gamma * var * tau
half_spread = self.gamma * var * tau / 2.0 + math.log1p(self.gamma / self.k) / self.gamma
bid = reservation - half_spread
ask = reservation + half_spread
```

Per wake-up the agent: reads best bid/ask to form the mid (falling back to a
reference price on an empty book), appends it to its mid history, computes
$\tau$, **cancels its still-resting quotes** (so it never stacks stale liquidity),
then posts a fresh POST_ONLY bid and ask.

- **$\sigma$ is estimated online** as the sample standard deviation of observed
  mids (unbiased, $n-1$), floored at `sigma_floor` so quotes never collapse to a
  zero spread.
- **$\gamma$ (risk aversion) and $k$ (liquidity / fill-intensity decay) are
  user-supplied** (`gamma=0.1`, `k=1.5` defaults). The implementation
  deliberately does **not** auto-fit $k$ from the book — a robust online estimate
  is out of scope, and silently fitting it would misrepresent the model.
- **$\tau = (T - t)/T$ is normalised to $[0, 1]$** and clamped at $0$ once the
  horizon passes, so the inventory term and spread shrink to their minimum near
  the close.

### 3.3 Running the comparison demo

`compare_market_makers(...)` runs **identical noise flow** against the A-S agent
and the naive `MarketMakerAgent` (which quotes a fixed `half_spread` symmetrically
around the mid — no reservation price, no skew), under a fixed seed:

```python
from abides_lite import compare_market_makers

results = compare_market_makers(symbol="AAPL", ref_price=150.0, seed=42, steps=4000)
as_inv    = results["avellaneda_stoikov"].agent_inventory   # mean-reverts toward 0
naive_inv = results["naive"].agent_inventory                # no inventory control
```

or from the CLI:

```bash
python python/abides_lite.py --compare-mm
```

The A-S maker's inventory mean-reverts toward zero because it skews quotes against
inventory; the naive maker's inventory random-walks. `KernelResult` also exposes
the full L3 `tape` (`TapeRecord` entries: orders / trades / cancels with
taker/maker agent attribution) and `agent_inventory` for downstream analysis.

---

## 4. Limitations (honest)

- **Finite-horizon, single-asset A-S.** This is the original 2008 model: one
  instrument, a fixed terminal horizon $T$. No multi-asset inventory or
  cross-hedging.
- **$k$ is not calibrated.** It is a user input; the model's spread is only as
  good as the supplied $k$.
- **$\sigma$ is a simple online estimate** (rolling sample stdev of mids), not a
  filtered or GARCH-type estimator.
- **$\tau$ is a dimensionless normalised horizon** in $[0,1]$. This is internally
  consistent (all the sign and monotonicity properties of §2 hold), but it
  rescales the magnitude of $\sigma^2 T$ relative to the paper's wall-clock-time
  convention, so the raw numeric spread is not directly comparable to a
  wall-clock-calibrated A-S.
- **No adverse-selection / toxic-flow model.** The agent does not distinguish
  informed from uninformed flow; in a real venue, fills are disproportionately
  adverse.
- **Not the infinite-horizon / multi-asset extension.** Guéant, Lehalle &
  Fernandez-Tapia (2013) generalise A-S to an infinite horizon and provide
  closed-form approximations better suited to a steady-state market maker — the
  natural next step from this implementation.

---

## References

- **Avellaneda, M. & Stoikov, S. (2008).** *High-frequency trading in a limit
  order book.* Quantitative Finance, 8(3), 217–224. (The model implemented here:
  reservation price and optimal spread.)
- **Ho, T. & Stoll, H. (1981).** *Optimal dealer pricing under transactions and
  return uncertainty.* Journal of Financial Economics, 9(1), 47–73. (The
  inventory-control precursor to A-S.)
- **Guéant, O., Lehalle, C.-A. & Fernandez-Tapia, J. (2013).** *Dealing with the
  inventory risk: a solution to the market making problem.* Mathematics and
  Financial Economics, 7(4), 477–507. (Infinite-horizon / multi-asset extension.)
- **Cartea, Á., Jaimungal, S. & Penalva, J. (2015).** *Algorithmic and
  High-Frequency Trading.* Cambridge University Press. (Textbook treatment of
  inventory-aware market making, including A-S and its extensions.)
