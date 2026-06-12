"""Render the daily catalyst research prompt (paste into an external LLM with web).

Pure/offline: it only renders already-loaded portfolio context into a structured ask.
The operator runs the result in Perplexity/Claude/ChatGPT and pastes the YAML back into
`catalyst-ingest`. ASCII-only output (Windows-safe).
"""

from __future__ import annotations

from datetime import date
from typing import Sequence, Tuple

from jinja2 import Template

CATALYST_PROMPT_TEMPLATE = Template("""\
You are a markets research assistant. Research TODAY's news and near-term catalysts for
the tickers below, using live web sources. Return ONLY a YAML document in EXACTLY the
schema shown at the end -- no preamble, no commentary outside the YAML.

Run date: {{ as_of }}
Generated-by hint (put in `generated_by`): {{ generated_by_hint }}

## Holdings (ticker | weight% | last price)
{% for t, w, p in held -%}
- {{ t }} | {{ "%.1f"|format(w) }}% | {{ "%.2f"|format(p) }}
{% endfor %}
{%- if watchlist %}

## Watchlist (sleeve names not currently held)
{% for t in watchlist -%}
- {{ t }}
{% endfor %}
{%- endif %}

## Known upcoming calendar events (for context; do not invent others)
{% if events -%}
{% for d, label, scope in events -%}
- {{ d }} | {{ scope }} | {{ label }}
{% endfor %}
{%- else -%}
No calendar events on file within the horizon.
{%- endif %}

## What to return
For EACH ticker above (held + watchlist), one `items` entry IF there is a real, sourced
catalyst; skip tickers with no news (do not fabricate). Add `macro` entries for
market-wide catalysts (rates/FOMC, broad tape, sector rotation). Use ONLY these enums:
direction = bull | bear | neutral; confidence = low | med | high. Cite a real
`source_url` for every claim. Dates are YYYY-MM-DD.

```yaml
as_of: {{ as_of }}
generated_by: {{ generated_by_hint }}
macro:
  - summary: "<market-wide catalyst>"
    direction: bull | bear | neutral
    event_date: YYYY-MM-DD        # optional
    source_url: "<url>"           # optional
items:
  - ticker: NVDA
    direction: bull | bear | neutral
    summary: "<one-line catalyst>"
    event_date: YYYY-MM-DD        # optional (earnings, launch, decision)
    confidence: low | med | high  # optional
    source_url: "<url>"           # optional
    notes: "<optional free text>"
freeform_notes: |
  <optional overall read of the tape>
```
""")


def generate_catalyst_prompt(
    *,
    as_of: date,
    held: Sequence[Tuple[str, float, float]],
    watchlist: Sequence[str],
    events: Sequence[Tuple[str, str, str]],
    generated_by_hint: str = "perplexity",
) -> str:
    """Render the research prompt. `held` = [(ticker, weight_pct, price)];
    `events` = [(iso_date, label, scope)]."""
    return CATALYST_PROMPT_TEMPLATE.render(
        as_of=as_of.isoformat(),
        held=list(held),
        watchlist=list(watchlist),
        events=list(events),
        generated_by_hint=generated_by_hint,
    )
