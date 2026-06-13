# Market Microstructure Features

This document specifies the limit-order-book (LOB) microstructure features computed in
two packages of this monorepo, which **share the same definitions**:

- **`packages/market-data`** — snapshot features over normalized exchange depth feeds
  (`src/features.py`) and trade-flow bar enrichment (`src/normalizer.py`).
- **`cpp/order-book`** — the same snapshot features computed directly off the live C++
  matching engine via its pybind11 binding (`python/orderbook/features.py`).

The math here has been audited and is implemented as described — this is *documentation*,
not a change. It is written for a programmer who knows what a limit order book is but has
not studied microstructure theory. For each signal we give the **formula**, the **reasoning**
(why it matters for prediction or execution), and **How to compute (this repo)** with the
real function signatures from *both* packages.

---

## Limit order book primer (brief)

A limit order book aggregates resting (unfilled) limit orders on two sides:

- **Bids** — buyers willing to pay up to some price. The **best bid** is the highest such price.
- **Asks** (offers) — sellers willing to sell down to some price. The **best ask** is the lowest.

At each price away from the touch there is **depth**: the total resting quantity at that level.
Bids are ordered best-first (highest price first), asks best-first (lowest price first), exactly
as exchanges deliver a partial-depth snapshot (`BookUpdate` in `normalizer.py`).

The gap between best ask and best bid is the **spread**. It is the round-trip **cost of
immediacy**: a trader who must transact *right now* posts a marketable order and crosses the
spread, paying (at least) half the spread relative to the mid. Resting (passive) liquidity earns
that half-spread in exchange for bearing adverse-selection and inventory risk. Almost every
feature below is, directly or indirectly, a way of measuring either *where the next trade is
likely to print* or *how expensive it is to trade now*.

---

## Price reference points

A single "the price" does not exist in a two-sided book; we use reference points that summarize
the touch.

### Mid price

$$ M = \frac{P_b + P_a}{2} $$

where $P_b$ is the best bid and $P_a$ the best ask. Simple, symmetric, and the conventional
anchor for spreads and returns. Its weakness: it **ignores size**. A book quoting 1000 lots bid
against 1 lot offered has the same mid as a balanced 1-vs-1 book, yet the next trade is far more
likely to lift the (thin) offer and tick up. Mid does not see that.

**How to compute (this repo)**

- `market-data` — `midprice(book: BookUpdate) -> float | None` returns `(bid.price + ask.price) / 2.0`,
  or `None` for a one-sided/empty/non-finite book.
- `order-book` — `mid_price(book: ob.OrderBook) -> float | None` returns `(bid + ask) / 2.0`
  off `get_best_bid()` / `get_best_ask()`.

### Size-weighted mid (weighted / imbalance-weighted mid)

$$ \text{wmid} = \frac{Q_b \cdot P_a + Q_a \cdot P_b}{Q_b + Q_a} $$

where $Q_b, Q_a$ are the resting sizes at the best bid and best ask. Note the **cross-weighting**:
each side's *price* is weighted by the *opposite* side's *size*. Define top-of-book imbalance

$$ I = \frac{Q_b}{Q_b + Q_a} \in [0, 1]. $$

Then the size-weighted mid can be rewritten as

$$ \text{wmid} = P_b\,(1 - I) + P_a\, I. $$

So **more bid size ($I \to 1$) pulls the value toward the ask $P_a$**. This is the correct sign and
deserves a sentence of intuition: a large resting bid is a wall of patient buyers; the marginal
trade is much more likely to *consume the offer* (an aggressive buy lifting $P_a$) than to push
through that bid wall. The price the book is "leaning toward" is therefore the ask. The
size-weighted mid encodes that lean, and it empirically tracks the next mid better than the plain
mid does at sub-second horizons.

**Honest naming.** This quantity is the **size-weighted mid** (a.k.a. weighted/imbalance-weighted
mid), a pure top-of-book function of the current snapshot. It is *related to but not identical to*
Stoikov's (2018) **micro-price**, $P^{\text{micro}}$. Stoikov defines the micro-price as the limit
of expected future mids conditioned on the current state, which works out to a **martingale-adjusted
estimator**

$$ P^{\text{micro}} = M + g(I, S) $$

i.e. the mid plus a correction $g$ that is a *learned/empirical* function of the imbalance $I$ and
the spread $S$ (estimated from data so the resulting price process is a martingale). The
size-weighted mid is the leading first-order approximation of that idea but omits the fitted
correction term. The repository computes the **weighted mid** and says so explicitly — both
`features.py` modules name the function `microprice` for API familiarity while documenting in their
docstrings that it is the size-weighted mid, **not** Stoikov's full estimator. We preserve that
distinction here.

**How to compute (this repo)**

- `market-data` — `microprice(book: BookUpdate) -> float | None`:
  `(bid.quantity * ask.price + ask.quantity * bid.price) / (bid.quantity + ask.quantity)`;
  `None` when one-sided/empty or both top sizes are zero.
- `order-book` — `microprice(book: ob.OrderBook) -> float | None`: same formula over the
  top-level `get_bid_depth(1)` / `get_ask_depth(1)` sizes; `None` when a side is empty or the
  combined top-of-book size is zero.

---

## Spread metrics

### Quoted spread (absolute and in basis points)

$$ S = P_a - P_b, \qquad S_{\text{bps}} = \frac{S}{M}\times 10^4. $$

The absolute quoted spread $S$ is the immediate cost of crossing. The **basis-point** form
normalizes by price so spreads are comparable **across instruments and across time**: a \$0.01
spread is tight on a \$5 name and astronomically wide on a \$70{,}000 one. Expressing it as a
fraction of the mid (×$10^4$ for bps) removes the price level and lets you compare BTC, ETH, and a
penny altcoin on one axis, or track liquidity regime changes within one symbol as its price drifts.

**Relation to effective / realized spread.** The *quoted* spread is what is *posted*; it is an
upper bound on what an aggressor typically pays, because large marketable orders may execute at the
volume-weighted touch and small ones may benefit from price improvement. The **effective spread**
($2 \times$ signed distance from trade price to the mid at execution) and the **realized spread**
(the same but measured against the mid a short horizon *after* the trade, isolating the
market-maker's revenue net of adverse selection) are the standard refinements. Both require **trade
data** (execution prices and a post-trade mid), so they are *not* computable from a pure depth
snapshot and are out of scope for `features.py`. We mention them only to place the quoted spread in
context.

**How to compute (this repo)**

- `market-data` — `quoted_spread(book) -> float | None` (`ask.price - bid.price`) and
  `quoted_spread_bps(book) -> float | None` (`spread / mid * 10_000.0`, `None` when mid ≤ 0).
- `order-book` — `quoted_spread(book: ob.OrderBook) -> float | None` returns `ask - bid` off the
  best-bid/best-ask accessors. (The C++ helper exposes the absolute spread; the bps normalization
  lives in the `market-data` layer.)

---

## Order-book imbalance

### Depth / volume imbalance

$$ \text{imbalance} = \frac{B - A}{B + A} \in [-1, +1], $$

where $B$ and $A$ are the **cumulative resting sizes** summed over the top-$N$ levels of the bid and
ask sides respectively ($N = 1$ recovers the classic top-of-book imbalance). The sign convention is:

- $> 0$ → **bid-heavy** → buy pressure (more resting demand than supply),
- $< 0$ → ask-heavy → sell pressure,
- $0$ → balanced; bounded to $[-1, +1]$ by construction.

**Why it predicts short-horizon moves.** This is one of the most robust empirical regularities in
high-frequency data: the instantaneous queue imbalance carries information about the *direction of
the next price move* at horizons from milliseconds to seconds. The intuition is the same lean
described for the weighted mid — a heavily one-sided book is more likely to be eaten on the thin
side. Cont, Kukanov & Stoikov (2014) show that order-book events at the touch have a linear price
impact in net order flow, and Cartea, Jaimungal & Penalva (2015) treat queue imbalance as a primary
predictive state variable for optimal execution and market making. As a feature it is cheap,
bounded, and (after sign) intuitive, which is why it is a staple input to short-horizon predictors
and RL execution observation spaces.

### Depth profile

The **depth profile** is the cumulative resting quantity per side, level by level away from the
touch:

$$ C_k = \sum_{j=1}^{k} q_j, $$

the running sum of level quantities $q_j$ from the top of book down to level $k$. This answers the
execution question *"how far would a market order walk the book?"* — $C_k$ is the liquidity
available before price slips to level $k$. It is the raw material for slippage/impact estimates and
for sizing orders against available depth.

**How to compute (this repo)**

- `market-data` — `depth_imbalance(book: BookUpdate, levels: int = 1) -> float | None` computes
  $(B-A)/(B+A)$ over top-`levels` cumulative sizes via the helper
  `cumulative_depth(levels: list[BookLevel], n: int | None = None) -> float | None`
  (sums the top-`n` resting sizes of one side; `None` for an empty side). `None` is returned for a
  one-sided/empty book or zero total depth.
- `order-book` — `volume_imbalance(book: ob.OrderBook, levels: int = 1) -> float | None` is the
  same $(B-A)/(B+A)$ over the cumulative quantities from `get_bid_depth(levels)` /
  `get_ask_depth(levels)`. The cumulative profile is `depth_profile(book, side, levels=10) ->
  list[ProfileLevel]`, where `side` is `"BUY"`/`"BID"` or `"SELL"`/`"ASK"` and each `ProfileLevel`
  carries `price`, `quantity`, and the running `cumulative` quantity from the top of book.

### Bundled snapshots

Both packages expose a one-call bundle for logging a feature row per snapshot/step:

- `market-data` — `compute_book_features(book: BookUpdate, levels: int = 5) -> BookFeatures`
  with fields `midprice`, `microprice`, `quoted_spread`, `quoted_spread_bps`, `bid_depth`,
  `ask_depth`, `imbalance_l1` (level-1), `imbalance` (top-`levels`), and `levels`. Every field
  degrades to `None` independently and the call never raises.
- `order-book` — `snapshot(book: ob.OrderBook, levels: int = 5) -> dict` with keys `best_bid`,
  `best_ask`, `spread`, `mid`, `microprice`, `imbalance` (level-1), `imbalance_topk` (top-`levels`),
  `bid_profile`, and `ask_profile`.

---

## Trade-flow / bar features

These are computed from **trades**, not the book, and live in `market-data`'s `normalizer.py` as
`BarFeatures`, built from the same trades as each 1-minute OHLCV bar (opt-in via
`enable_bar_features`).

### VWAP

$$ \text{VWAP} = \frac{\sum_i p_i\, q_i}{\sum_i q_i} $$

over the trades $i$ in the bar (price $p_i$, quantity $q_i$). The **volume-weighted average price**
is the standard execution benchmark: a large parent order is judged by whether its average fill
beat or lagged the period's VWAP, because VWAP is the price a "neutral" participant who spread
their order evenly across volume would have achieved. Beating VWAP means you sourced liquidity
better than the average market participant over that window; this is why VWAP is both a benchmark
and a scheduling target (VWAP execution algorithms). When the bar has **zero volume** (e.g. dust
trades reported with quantity 0), the code falls back to the bar **close** rather than dividing by
zero.

### Signed volume / buy–sell imbalance

$$ \text{imbalance} = \frac{V_{\text{buy}} - V_{\text{sell}}}{V_{\text{total}}} \in [-1, +1], $$

where $V_{\text{buy}}$ and $V_{\text{sell}}$ are base-asset volumes split by the **taker/aggressor
side** — the side that crossed the spread and initiated the trade. Net aggressive buying drives
price up and net aggressive selling drives it down, so signed trade flow is a direct read on
realized demand pressure over the bar (a trade-side analogue of book imbalance).

**The accuracy advantage — no trade-side inference needed.** The aggressor side is the crux. In
equities, exchange tapes historically did **not** label which counterparty was the aggressor, so
practitioners *infer* it with the **Lee & Ready (1991)** algorithm (the quote/tick rule: compare the
trade price to the prevailing mid, falling back to the tick test on ties). That inference is
imperfect and introduces classification error into every flow-based feature. In crypto, the
**feed reports the aggressor directly** — Binance's trade message carries the *buyer-is-maker* flag,
from which the taker side follows exactly — so `Trade.side` is the **true** aggressor side, not an
estimate. Consequently `BarFeatures` does the buy/sell split as **exact flow signing** and the
Lee–Ready tick rule is **unnecessary here**. This is a genuine, statable accuracy improvement over
the equities workflow: the feature has no trade-classification error to begin with.

**How to compute (this repo)**

- `market-data` — `BarFeatures(buy_volume, sell_volume, imbalance, vwap)` is produced by
  `TickNormalizer._compute_bar_features(trades, bar)`: `buy_volume` / `sell_volume` sum
  `Trade.quantity` filtered on `Trade.side == "buy"` / `"sell"`; `imbalance` is
  `(buy_volume - sell_volume) / total` (or `0.0` when total is 0); `vwap` is
  `notional / bar.volume` (or `bar.close` when volume is 0). Retrieve via
  `TickNormalizer.pop_bar_features(symbol) -> BarFeatures | None`. There is no order-book
  equivalent — these require the trade stream, which the C++ engine layer does not carry.

---

## Honest scope note

Every feature above is **snapshot-level** (book features) or **bar-aggregate** (trade-flow
features). The depth/volume imbalance in particular is a *static* function of the current resting
sizes. It is **not** event-level **Order Flow Imbalance (OFI)** in the Cont–Kukanov–Stoikov sense.
True OFI is the signed *increment* in best-level size attributable to discrete order-book
**events** — adds, cancels, and trades *at the touch* — obtained by **differencing consecutive
best-level snapshots against an incrementally maintained book**. That construction captures *changes*
in liquidity (a cancel that thins the offer, an add that deepens the bid) which a sequence of
independent depth snapshots cannot disentangle; a 100 ms partial-depth stream gives you the *level*,
not the per-event *flow*. The source docstrings state this explicitly, and `depth_imbalance` /
`volume_imbalance` must **not** be presented as OFI. If event-level OFI is required, it has to be
built on the incremental order book, not on these snapshot features.

---

## References

- **Stoikov, S. (2018).** *The Micro-Price: Estimating the Fair Price of an Asset.*
  *Quantitative Finance*, 18(12), 1959–1966. — Defines the martingale-adjusted micro-price
  $M + g(I, S)$; the size-weighted mid in this repo is its first-order approximation, not the full
  estimator.
- **Cont, R., Kukanov, A., & Stoikov, S. (2014).** *The Price Impact of Order Book Events.*
  *Journal of Financial Econometrics*, 12(1), 47–88. — Linear price impact of net order flow / OFI
  at the touch; basis for the imbalance-predicts-returns evidence and the event-level OFI distinction.
- **Lee, C. M. C., & Ready, M. J. (1991).** *Inferring Trade Direction from Intraday Data.*
  *Journal of Finance*, 46(2), 733–746. — The classic equities trade-signing algorithm, used when
  the aggressor is unknown; unnecessary here because the crypto feed reports the aggressor side.
- **Cartea, Á., Jaimungal, S., & Penalva, J. (2015).** *Algorithmic and High-Frequency Trading.*
  Cambridge University Press. — Queue imbalance as a predictive state variable for execution and
  market making; spread as the cost of immediacy.
- **O'Hara, M. (1995).** *Market Microstructure Theory.* Blackwell. — Foundational treatment of
  spreads, adverse selection, and price formation in quote-driven and order-driven markets.
