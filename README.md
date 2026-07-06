# mireye-site-screen
# Site Screen — franchise/commercial site feasibility pre-check

Built on the [Mireye](https://www.mireye.com) API for Mireye's take-home assignment.

## The problem

Before a franchise (or any commercial retail operator) leases or buys a candidate lot, they usually need a full site-selection study — demographics, traffic, environmental, zoning — which costs real money and time. Most of that cost goes toward answering one prior question first: **can this specific lot even be legally and physically built on at all?**

Site Screen answers that first question in seconds, for free, using only federal public data — before anyone commissions a full study. It's a cheap first-pass filter, not a replacement for the full study.

## What it checks

Given a latitude/longitude, it pulls Mireye's `site_selection` field set and flags:

- FEMA floodplain status
- Wetland intersection (USFWS NWI)
- Protected area / critical habitat / conservation easement conflicts
- Slope (buildability / grading cost)
- Distance to the nearest major road (frontage/access)
- Parcel size
- Zoning (fetched explicitly — see **Findings** below)
- Nearby commercial density, schools, groceries (informational context)

It then prints a plain-English read (via `/v1/ask`) alongside the deterministic flags, and saves a shareable HTML report.

## What it does NOT check

Mireye has no demographic, foot-traffic, or competitor data. This tool answers "can I build here," not "will this location make money." Those require a separate site-selection study — this tool is meant to filter out physically/legally dead-on-arrival sites *before* paying for that study.

## Findings from testing

- **`/v1/fetch` with the `site_selection` preset does not return `parcel_zoning`**, even when explicitly requested via the `fields` parameter — despite zoning being arguably the single most decision-critical field for site selection. `/v1/ask`, by contrast, was able to answer zoning correctly for the same coordinate. This is a real gap between what the two endpoints can see for the same query, worth a look.
- `/v1/ask` is noticeably slower than `/v1/fetch` (had to raise the timeout from 30s to 60s to avoid failures) — expected for an LLM-backed endpoint vs. a data lookup, but worth knowing for anyone building latency-sensitive tooling on top.
- The POI proximity fields (`poi_count_1km`, nearest school/grocery/hospital) were a pleasant surprise — closer to a rough commercial-context signal than I expected Mireye to have.

## How to run it

Requires Python 3 and one library:

```
pip install requests
```

1. Get a Mireye API token at mireye.com (account settings → API tokens)
2. Open `site_screen.py`, paste your token into the `TOKEN = "..."` line near the top
3. Run it:
   ```
   python site_screen.py
   ```
4. Enter a latitude, longitude, and optional nickname when prompted
5. It prints the report to the terminal and saves `site_report.html` next to the script (auto-opens in your browser)

An example output is included: `example_output.html`.

## Built by

Keval Patel with Mireye software
