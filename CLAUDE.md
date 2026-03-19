# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (opens browser automatically on Windows)
start.bat

# Or run directly
python app.py
```

The server starts on `http://127.0.0.1:5000` (port configurable via `PORT` env var).

## Architecture

Single-file Flask backend (`app.py`) + single Jinja template (`templates/index.html`).

**Data flow:**
1. User pastes Steam inventory HTML (with SIH price badge markup) + moon.market item list text into the UI
2. Frontend POSTs to `/analyze` with `{inv_html, moon_text, min_percent, steam_id}`
3. Backend fetches live inventory from Steam API (`load_inventory_names`) — cached 5 min per steam_id
4. Parses Steam prices from the pasted HTML via regex on `data-price` attributes (`load_steam_prices_from_html`)
5. Parses moon.market prices from text (alternating name/price lines, `load_moon_prices`)
6. `run_analysis` zips inventory items with steam prices positionally, groups by name, computes moon payout ratios
7. Returns JSON with 5 item lists (`can_sell`, `cant_sell`, `filtered`, `best`, `worst`) + summary `totals`

**Key constants in `app.py`:**
- `APP_ID = 252490` — Rust on Steam
- `CONTEXT_ID = 2` — Steam inventory context
- `MOON_FEE_PERCENT = 0.04`, `MOON_FEE_FIXED = 1.0` — fee applied to gross moon payout

**Frontend (`templates/index.html`):**
- Vanilla JS + Bootstrap 5, no build step
- Five tabs rendered client-side from the API response
- Bundle feature: click rows to aggregate selected items; shows totals in a fixed bottom bar
- Sortable columns, per-tab search filter
- Item icons loaded from Steam CDN using `icon_url` from the inventory API

## Moon Price Format

The moon text input expects alternating lines:
```
Item Name
12.34 $
Product
Another Item
5.00 $
```
The word "Product" between entries is optional and skipped.
