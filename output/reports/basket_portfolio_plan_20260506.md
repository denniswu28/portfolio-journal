# Basket Portfolio Plan - 2026-05-06

Educational planning note, not financial advice. This redesign uses the current Fidelity positions export at `data/portfolio_snapshots/2026-05-06/positions.csv`, the rules in `config/persistent_context.yaml`, and the Boist-derived optimizer universe in `config/growth_universe.yaml`.

This version replaces the broad `AI Future` / `Tech` structure with a compute-demand map. The dominant thesis is that agentic AI increases demand for CPU cycles, memory, storage, packaging, data-center capacity, electricity, cooling, networking, and security. Baskets should therefore separate the Boist core from the second-order infrastructure effects instead of mixing unrelated future-growth themes together.

## Planning Assumptions

- Displayed positions/cash total from the CSV, excluding `Pending activity`: $19,447.75.
- Pending activity: -$1,089.24.
- Effective planning total after pending activity: $18,358.51.
- Displayed SPAXX cash: $3,531.72.
- Effective cash after pending activity: $2,442.48.
- Target cash reserve: $1,835.88, or 10.00% of effective planning total.
- Net trade bias in this plan: spend about $606.60 of effective cash, including the planned $175.76 METD META-bear hedge.
- Non-cash targets are scaled to about 95.74% of the previous version so the cash reserve returns to roughly 10% without changing the thesis mix.
- Hard structure rule: every individual company stock must be inside a basket; only index funds, index ETFs, sector ETFs, bond ETFs, commodity ETFs, and cash may sit outside baskets.
- Existing constraints respected: no options, single-name shorts allowed only after Fidelity borrow/margin/liquidity checks, no single equity or equity ETF position above 10% of effective planning total, cash reserve inside the 5-15% range, and targeted single-name hedges capped at a small tactical size. Gold and precious-metal hedges are exempt from the equity concentration cap, but still require explicit sizing rationale.

## Redesign Logic

The previous plan correctly kept memory, semiconductors, CPU/foundry, and metals alive, but it still under-expressed the derived effects of agentic compute demand. The biggest redesign is to promote power delivery, electrical and thermal equipment, data-center facilities, energy, uranium, and industrial inputs from small fragments into explicit baskets.

The plan also cuts down duplicated broad tech exposure. AAPL, GOOG, QQQM, IYW, VGT, and PLTR all behave like AI-platform or broad tech beta in a risk-off move, so they no longer get a large combined allocation. GLDM is not forced below 10% because gold is a non-equity hedge; any GLDM trim in this plan is a funding and hedge-sizing choice, not an equity concentration rule.

This implementation adds a 1.00% targeted short hedge against META-led AI losers and expensive AI-capex beneficiaries that may spend heavily but fail to monetize AI. The hedge avoids broad QQQ or S&P inverse ETFs because the core thesis still expects compute-heavy winners and QQQ beta to rise.

## Derived Demand Map

| Compute-demand effect | Basket expression |
|---|---|
| More CPU cycles and heterogeneous compute | `CPU, Foundry, and Advanced Packaging` |
| Memory and storage scarcity | `Boist Memory and Storage Shortage` |
| Semiconductor supply-chain beta | `Semiconductor Beta and ASIC Supply Chain` |
| AI customers monetizing agent demand | `AI Platforms and Agent Customers` |
| Higher rack density, more heat, more facility capex | `Data-Center Power, Grid, and Cooling` |
| Electricity load and input scarcity | `Energy, Uranium, and Industrial Inputs` |
| Higher autonomous-system and geopolitical risk | `Defense, Autonomy, and Aerospace` |
| AI capex without durable monetization | `AI Platforms and Agent Customers` |
| Portfolio drawdown and macro risk | `Precious Metals Hedge`, `Treasury and TIPS Ballast`, cash |

## Target Portfolio Summary

| Segment | Target funds | Effective account weight | Role |
|---|---:|---:|---|
| Boist Memory and Storage Shortage | $2,010.45 | 10.95% | DRAM, NAND, HBM, storage, and memory-pricing rerating |
| CPU, Foundry, and Advanced Packaging | $2,058.32 | 11.21% | CPU mix shift, foundry capacity, packaging, and semi equipment |
| Semiconductor Beta and ASIC Supply Chain | $1,866.85 | 10.17% | Broad semiconductor beta plus ASIC/networking suppliers |
| AI Platforms and Agent Customers | $1,204.92 | 6.56% | AI demand customers plus embedded META-led loser hedge |
| Data-Center Power, Grid, and Cooling | $2,201.92 | 11.99% | Grid upgrades, switchgear, thermal gear, data centers, cyber |
| Energy, Uranium, and Industrial Inputs | $1,340.30 | 7.30% | Power generation, uranium, copper, metals, and commodities |
| Defense, Autonomy, and Aerospace | $813.75 | 4.43% | Related hedge for autonomy, sensors, aerospace, geopolitics |
| Precious Metals Hedge | $2,201.92 | 11.99% | Crisis, currency, and real-rate hedge with gold exempt from the equity cap |
| Treasury and TIPS Ballast | $789.82 | 4.30% | Duration and inflation-linked ballast |
| Core US index outside baskets | $1,531.77 | 8.34% | Broad market anchor below the equity-position limit |
| Core ex-US index outside baskets | $502.61 | 2.74% | Small international diversification sleeve |
| Cash reserve | $1,835.88 | 10.00% | Tactical reserve restored to roughly 10% after proportional sleeve trims |
| Total | $18,358.51 | 100.00% | Effective plan total after pending activity |

Core Boist baskets total $7,140.54, or 38.89% of effective capital, including the embedded METD hedge inside the AI platforms basket. Directly connected derivative-demand baskets total $4,355.97, or 23.73%. The rest is ballast, index exposure, and cash.

## Target Baskets

### Boist Memory and Storage Shortage

Target funds: $2,010.45

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| SNDK | 25.00% | $502.61 | $501.54 | Buy $1.07 |
| MU | 22.00% | $442.30 | $377.56 | Buy $64.74 |
| WDC | 18.00% | $361.88 | $394.96 | Sell $33.08 |
| STX | 18.00% | $361.88 | $426.43 | Sell $64.55 |
| SIMO | 17.00% | $341.78 | $255.12 | Buy $86.66 |
| P | 0.00% | $0.00 | $130.42 | Sell $130.42 |

Basket rationale: this is the pure memory/storage scarcity expression. P is removed because it does not fit the thesis. SNDK stays the largest weight, but the basket adds to MU and SIMO rather than only chasing the hottest winner.

### CPU, Foundry, and Advanced Packaging

Target funds: $2,058.32

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| TSM | 31.00% | $638.08 | $418.47 | Buy $219.61 |
| INTC | 24.00% | $493.99 | $0.00 | Buy $493.99 |
| SOXX | 19.00% | $391.08 | $0.00 | Buy $391.08 |
| AMD | 16.00% | $329.33 | $0.00 | Buy $329.33 |
| ASML | 10.00% | $205.84 | $0.00 | Buy $205.84 |

Basket rationale: this is the cleanest response to the Boist CPU/foundry/packaging claim. TSM remains the current anchor. INTC and AMD are first-tranche adds, SOXX reduces single-name timing risk, and ASML adds equipment exposure.

### Semiconductor Beta and ASIC Supply Chain

Target funds: $1,866.85

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| SMH | 80.00% | $1,493.48 | $1,696.68 | Sell $203.20 |
| AVGO | 10.00% | $186.69 | $0.00 | Buy $186.69 |
| MRVL | 10.00% | $186.68 | $0.00 | Buy $186.68 |

Basket rationale: SMH remains the main semiconductor beta expression, but the redesign adds direct ASIC/networking exposure through AVGO and MRVL. The SMH trim is small because semis remain central, but the risk is spread a bit better.

### AI Platforms and Agent Customers

Target funds: $1,204.92

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| GOOG | 29.89% | $360.21 | $851.11 | Sell $490.90 |
| QQQM | 17.08% | $205.83 | $618.92 | Sell $413.09 |
| PLTR | 17.08% | $205.83 | $316.51 | Sell $110.68 |
| METD | 14.59% | $175.76 | $0.00 | Buy $175.76 |
| VGT | 12.81% | $154.37 | $418.77 | Sell $264.40 |
| AAPL | 8.54% | $102.92 | $837.76 | Sell $734.84 |
| IYW | 0.00% | $0.00 | $631.03 | Sell $631.03 |

Basket rationale: this sleeve is intentionally smaller on net platform exposure while now housing the META-led hedge. The portfolio was already long broad platform/tech beta. The Boist thesis says the highest-conviction forward bottlenecks are compute, memory, and power infrastructure, so broad platform exposure is harvested to fund those bottlenecks. METD stays inside this basket because the loser thesis is directly tied to AI-platform capex without durable monetization, not to a broad QQQ short.

Instrument note: `METD` is the verified direct META bear ETF and is the planned hedge. Fidelity may also allow individual stock shorts; if direct META shorting is available, it can replace METD only after confirming borrow availability, margin impact, short-sale rules, locate/recall risk, and a hard exit plan. Do not replace this hedge with broad QQQ or S&P inverse ETFs.

AI-loser shortlist by sector:

| Sector / theme | Shortlist | Why it may lose from AI | Current action |
|---|---|---|---|
| Social / ad platforms | META, SNAP | Heavy AI infrastructure spend, weak direct monetization path, ad-market cyclicality, regulatory pressure | Active METD hedge; SNAP watchlist |
| Legacy creative and seat SaaS | ADBE, CRM | Generative AI can compress seat pricing, reduce workflow scarcity, and weaken high-multiple software narratives | Watchlist; no trade until earnings or product-pricing thesis breaks |
| IT services / outsourcing | ACN, EPAM or CTSH | AI agents reduce billable labor demand and pressure project-based pricing | Watchlist; prefer no trade without a clear revenue/margin break |
| Low-moat content / education | CHGG, COUR | AI-generated content and tutoring reduce scarcity and pricing power | Watchlist only; too idiosyncratic for the starter hedge |
| Legacy media / streaming | WBD, PARA | Weak balance sheets, weak ad leverage, and limited direct AI monetization | Watchlist only |
| Consumer marketplaces | ETSY, EBAY | Weak growth, higher ad/customer-acquisition pressure, limited proprietary AI advantage | Watchlist only |
| Consumer hardware / devices | AAPL | AI strategy risk exists, but ecosystem and cash flow are too strong for a short here | Trim only, not hedge target |
| Search / cloud mega-cap | None; GOOG is a trim only | Search disruption risk exists, but cloud, model, and infrastructure assets can still benefit | No short |
| Semis, memory, power, grid, energy, defense, cyber | None | These remain Boist-derived beneficiaries or protective sleeves | No short |

Hedge rule: the embedded METD sleeve starts near 1.00% economic short exposure and should normally keep the combined basket within target. Do not raise the hedge above 3% without rewriting the plan. Do not fund it by cutting memory, CPU/foundry, semiconductor, or power-grid baskets below target. Close or reduce the hedge if META shows credible AI monetization, if the borrow/spread becomes unfavorable on any direct short, or if the hedge loses 15-20% from entry.

### Data-Center Power, Grid, and Cooling

Target funds: $2,201.92

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| GRID | 30.00% | $660.58 | $73.61 | Buy $586.97 |
| PAVE | 25.00% | $550.48 | $0.00 | Buy $550.48 |
| SRVR | 15.00% | $330.29 | $70.62 | Buy $259.67 |
| IHAK | 10.00% | $220.19 | $37.70 | Buy $182.49 |
| VRT | 10.00% | $220.19 | $0.00 | Buy $220.19 |
| ETN | 10.00% | $220.19 | $0.00 | Buy $220.19 |

Basket rationale: this is the major redesign. Agentic compute demand is not just a chip trade. It becomes a grid, switchgear, power-delivery, cooling, data-center, and security trade. GRID and PAVE keep the basket ETF-heavy; VRT and ETN add targeted thermal/electrical equipment exposure.

### Energy, Uranium, and Industrial Inputs

Target funds: $1,340.30

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| DBB | 25.00% | $335.08 | $184.72 | Buy $150.36 |
| XME | 20.00% | $268.06 | $164.57 | Buy $103.49 |
| XLE | 18.00% | $241.25 | $45.10 | Buy $196.15 |
| URNM | 17.00% | $227.85 | $53.08 | Buy $174.77 |
| DBA | 10.00% | $134.03 | $71.51 | Buy $62.52 |
| CCJ | 10.00% | $134.03 | $0.00 | Buy $134.03 |

Basket rationale: if data-center power becomes the constraint, generation, uranium, fuel, copper, steel, aluminum, and resource equities become second-order beneficiaries. This basket is larger than the old real-assets sleeve because it now has a direct link to the compute-demand thesis.

### Defense, Autonomy, and Aerospace

Target funds: $813.75

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| PPA | 30.00% | $244.13 | $377.17 | Sell $133.04 |
| SHLD | 25.00% | $203.44 | $341.52 | Sell $138.08 |
| ITA | 22.50% | $183.09 | $352.63 | Sell $169.54 |
| XAR | 22.50% | $183.09 | $354.28 | Sell $171.19 |

Basket rationale: defense stays as a related hedge, but the target is smaller because the dominant thesis is compute infrastructure rather than a pure geopolitical-defense allocation. PLTR moves to `AI Platforms and Agent Customers`.

### Precious Metals Hedge

Target funds: $2,201.92

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| GLDM | 91.30% | $2,010.35 | $2,782.73 | Sell $772.38 |
| IAUI | 4.35% | $95.78 | $656.87 | Sell $561.09 |
| SLV | 2.61% | $57.47 | $113.27 | Sell $55.80 |
| PLTM | 1.74% | $38.32 | $72.27 | Sell $33.95 |

Basket rationale: gold is exempt from the 10% equity concentration rule because it is a non-equity macro hedge. GLDM therefore remains above 10% of effective capital on purpose. The trim is only to fund higher-conviction Boist-derived buys while restoring cash to roughly 10%.

### Treasury and TIPS Ballast

Target funds: $789.82

| Component | Target weight in basket | Target dollars | Current dollars | Action |
|---|---:|---:|---:|---:|
| TIP | 70.00% | $552.87 | $76.70 | Buy $476.17 |
| IEF | 30.00% | $236.95 | $32.01 | Buy $204.94 |

Basket rationale: the Boist core and derivative sleeves are intentionally equity-heavy. This small ballast basket absorbs some macro shock without turning the account into a bond portfolio.

## Outside Index, ETF, Fund, and Cash Holdings

These are allowed outside baskets because they are index funds, index ETFs, or cash-like holdings.

| Holding | Target dollars | Effective account weight | Current dollars | Action |
|---|---:|---:|---:|---:|
| FXAIX | $1,531.77 | 8.34% | $2,067.84 | Sell $536.07 |
| VXUS | $502.61 | 2.74% | $112.55 | Buy $390.06 |
| SPAXX / cash reserve | $1,835.88 | 10.00% | $2,442.48 effective | Spend $606.60 |

Outside-holding rationale: FXAIX remains the broad US anchor but is brought below the 10% equity-position threshold. VXUS is increased to a small diversification sleeve. QQQM moves into the AI-platform basket instead of sitting outside as a broad tech duplicate.

## Basket Operations Plan

1. Rename `Storage and Memory` to `Boist Memory and Storage Shortage`.
2. Keep SNDK, MU, WDC, STX, and SIMO in the memory basket.
3. Sell P; it is not part of the memory/storage thesis.
4. Create `CPU, Foundry, and Advanced Packaging`.
5. Move TSM from `Tech` into `CPU, Foundry, and Advanced Packaging`.
6. Add INTC, AMD, SOXX, and ASML to the CPU/foundry basket.
7. Create `Semiconductor Beta and ASIC Supply Chain`.
8. Move every SMH lot from `Tech`, `AI Future`, and unbasketed holdings into the semiconductor basket.
9. Add AVGO and MRVL to the semiconductor/ASIC basket.
10. Create `AI Platforms and Agent Customers`.
11. Move GOOG, AAPL, QQQM, VGT, IYW, and PLTR into the AI-platform basket before trimming.
12. Create `Data-Center Power, Grid, and Cooling`.
13. Move GRID, SRVR, and IHAK from `AI Future` into the data-center basket.
14. Add PAVE, VRT, and ETN to the data-center basket.
15. Create `Energy, Uranium, and Industrial Inputs`.
16. Move DBA, DBB, URNM, and XLE from `AI Future` into the energy/materials basket.
17. Move XME from unbasketed holdings into the energy/materials basket.
18. Add CCJ to the energy/materials basket.
19. Rename `US Defense` to `Defense, Autonomy, and Aerospace`.
20. Move the PPA lot from `AI Future` into the defense basket.
21. Move PLTR out of defense and into `AI Platforms and Agent Customers`.
22. Rename `Gold and Metal` to `Precious Metals Hedge`.
23. Move the GLDM lot from `AI Future` and unbasketed SLV/PLTM into the precious-metals basket.
24. Buy about $175.76 of METD inside `AI Platforms and Agent Customers` as the embedded META bear hedge. If Fidelity allows direct individual stock shorts, direct META shorting can be considered later with documented borrow, margin, locate/recall, liquidity, sizing, and stop rules. Do not use PSQ, SH, or broad QQQ/S&P inverse ETFs for this hedge.
25. Create `Treasury and TIPS Ballast`.
26. Move TIP and IEF from `AI Future` into the Treasury/TIPS basket.
27. Move VXUS out of `AI Future` and hold it outside baskets as a broad ex-US index ETF.
28. Keep FXAIX outside baskets as the core US index fund.
29. Empty and delete `AI Future` after all positions are moved.
30. Retire the old `Tech` basket after all components are relocated.

## Trade Plan

Use Fidelity's position movement tools first. Moving an existing position into a new basket is preferred over selling and rebuying when the target is close. After the basket structure is clean, use basket rebalance or targeted dollar trades to close the gaps.

### High Priority Trades

| Action | Reason |
|---|---|
| Sell about $772 of GLDM | Makes only a funding trim; GLDM remains above 10% because gold is exempt from the equity concentration cap. |
| Sell about $561 of IAUI | Uses the smaller gold-strategy sleeve as funding while keeping GLDM as the primary gold hedge. |
| Sell about $735 of AAPL | Reduces broad platform concentration and funds higher-conviction compute bottlenecks. |
| Sell about $631 of IYW | Removes duplicate broad tech ETF exposure. |
| Buy about $587 of GRID | Makes grid and power delivery a real derivative sleeve instead of a token holding. |
| Buy about $550 of PAVE | Adds infrastructure/electrical buildout exposure tied to data-center demand. |
| Buy about $494 of INTC | Adds CPU/foundry/packaging exposure emphasized by the Boist report. |
| Buy about $476 of TIP | Adds duration ballast while gold remains the larger non-equity hedge. |
| Sell about $536 of FXAIX | Keeps broad US index exposure below the equity-position threshold. |
| Sell about $491 of GOOG | Harvests platform overweight and funds compute-infrastructure bottlenecks. |
| Buy about $390 of VXUS | Restores small international ballast outside the Boist baskets. |
| Buy about $391 of SOXX | Adds diversified CPU/foundry/semi equipment exposure. |
| Buy about $176 of METD | Starts a targeted META-led hedge inside `AI Platforms and Agent Customers` without shorting QQQ. |
| Sell all P, about $130 | Removes a non-thesis individual stock from the memory basket. |

### Secondary Rebalance Trades

| Symbol | Approx action |
|---|---:|
| AMD | Buy $329.33 inside `CPU, Foundry, and Advanced Packaging` |
| SRVR | Buy $259.67 inside `Data-Center Power, Grid, and Cooling` |
| VGT | Sell $264.40 inside `AI Platforms and Agent Customers` |
| TSM | Buy $219.61 inside `CPU, Foundry, and Advanced Packaging` |
| VRT | Buy $220.19 inside `Data-Center Power, Grid, and Cooling` |
| ETN | Buy $220.19 inside `Data-Center Power, Grid, and Cooling` |
| ASML | Buy $205.84 inside `CPU, Foundry, and Advanced Packaging` |
| IEF | Buy $204.94 inside `Treasury and TIPS Ballast` |
| XLE | Buy $196.15 inside `Energy, Uranium, and Industrial Inputs` |
| AVGO | Buy $186.69 inside `Semiconductor Beta and ASIC Supply Chain` |
| MRVL | Buy $186.68 inside `Semiconductor Beta and ASIC Supply Chain` |
| IHAK | Buy $182.49 inside `Data-Center Power, Grid, and Cooling` |
| URNM | Buy $174.77 inside `Energy, Uranium, and Industrial Inputs` |
| DBB | Buy $150.36 inside `Energy, Uranium, and Industrial Inputs` |
| ITA | Sell $169.54 inside `Defense, Autonomy, and Aerospace` |
| XAR | Sell $171.19 inside `Defense, Autonomy, and Aerospace` |
| CCJ | Buy $134.03 inside `Energy, Uranium, and Industrial Inputs` |
| SMH | Sell $203.20 inside `Semiconductor Beta and ASIC Supply Chain` |
| SHLD | Sell $138.08 inside `Defense, Autonomy, and Aerospace` |
| PPA | Sell $133.04 inside `Defense, Autonomy, and Aerospace` |
| XME | Buy $103.49 inside `Energy, Uranium, and Industrial Inputs` |
| SIMO | Buy $86.66 inside `Boist Memory and Storage Shortage` |
| PLTR | Sell $110.68 inside `AI Platforms and Agent Customers` |
| MU | Buy $64.74 inside `Boist Memory and Storage Shortage` |
| DBA | Buy $62.52 inside `Energy, Uranium, and Industrial Inputs` |
| SLV | Sell $55.80 inside `Precious Metals Hedge` |
| STX | Sell $64.55 inside `Boist Memory and Storage Shortage` |
| PLTM | Sell $33.95 inside `Precious Metals Hedge` |
| SNDK | Buy $1.07 inside `Boist Memory and Storage Shortage` |
| WDC | Sell $33.08 inside `Boist Memory and Storage Shortage` |

For deltas below about $25, it is reasonable to let the basket rebalance or the next contribution handle the adjustment rather than forcing a tiny manual trade.

## Staging Rules

1. New single-stock adds can be staged in two tranches: 50-60% now, the rest after a semiconductor or market pullback, unless the user intentionally wants full target immediately.
2. Prioritize ETF-based adds first when execution confidence is low: GRID, PAVE, SOXX, TIP, IEF, VXUS, XLE, DBB, XME, URNM.
3. Do not add to SNDK aggressively above target unless it pulls back while the memory rerating thesis remains intact.
4. If SMH rises sharply without earnings support, rebalance rather than add.
5. If effective cash falls below 9.5%, pause discretionary new buys until cash is restored; if it stays above 10%, remaining buys can be staged normally.
6. GLDM may remain above 10% because gold is not an equity position; rebalance it only against the precious-metals target and funding needs, not against the equity cap.
7. If power/grid names gap up before execution, buy the ETF sleeve first and defer VRT/ETN individual-stock adds.
8. Do not use broad index inverse ETFs such as PSQ or SH while the plan expects QQQ and compute winners to rise.
9. Keep the META short tactical: buy about $175.76 of METD for the starter hedge; consider direct META shorting only after Fidelity confirms borrow, margin, locate/recall, and liquidity terms.
10. Review the META hedge daily because inverse single-stock ETF products reset daily and can decay or gap sharply.
11. Reduce or close the hedge if META breaks above the entry setup, if AI monetization improves, if borrow/spreads worsen on any direct short, or if the hedge loses 15-20% from entry.

## Execution Order

1. Move all existing positions into their target baskets without trading where possible.
2. Empty and delete `AI Future` only after every holding has been moved.
3. Retire `Tech` only after AAPL, GOOG, QQQM, IYW, SMH, TSM, and VGT are relocated.
4. Execute the equity concentration and funding sells first: AAPL, IYW, GOOG, QQQM, FXAIX, defense trims, P, and the smaller GLDM/IAUI precious-metals funding trims.
5. Execute the core Boist buys: INTC, SOXX, AMD, ASML, TSM, MU, SIMO.
6. Execute derivative-demand ETF buys: GRID, PAVE, SRVR, XLE, URNM, DBB, XME, VXUS, TIP, IEF.
7. Add METD inside `AI Platforms and Agent Customers`; do not substitute PSQ or SH. Direct META shorting can be considered later only if Fidelity confirms borrow/margin terms.
8. Add targeted single-name derivative exposure only after ETF exposure is in place: VRT, ETN, AVGO, MRVL, CCJ.
9. Confirm effective cash remains near $1,836, or roughly 10% of effective planning total.

## Final Target Basket List

- `Boist Memory and Storage Shortage`
- `CPU, Foundry, and Advanced Packaging`
- `Semiconductor Beta and ASIC Supply Chain`
- `AI Platforms and Agent Customers`
- `Data-Center Power, Grid, and Cooling`
- `Energy, Uranium, and Industrial Inputs`
- `Defense, Autonomy, and Aerospace`
- `Precious Metals Hedge`
- `Treasury and TIPS Ballast`

Outside baskets:

- FXAIX
- VXUS
- SPAXX / cash reserve

Delete after migration:

- `AI Future`
- `Tech`