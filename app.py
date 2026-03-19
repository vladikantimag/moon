import re
import time
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

STEAM_ID = "76561199482415281"
APP_ID = 252490
CONTEXT_ID = 2
MOON_FEE_PERCENT = 0.04
MOON_FEE_FIXED = 1.0

_inventory_cache: dict = {}
_inventory_cache_time: dict = {}
CACHE_TTL = 300  # 5 минут


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def load_inventory_names(steam_id: str) -> List[dict]:
    global _inventory_cache, _inventory_cache_time
    if steam_id in _inventory_cache and time.time() - _inventory_cache_time.get(steam_id, 0) < CACHE_TTL:
        return _inventory_cache[steam_id]
    url = f"https://steamcommunity.com/inventory/{steam_id}/{APP_ID}/{CONTEXT_ID}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    assets = data.get("assets", [])
    descriptions = data.get("descriptions", [])
    desc_map: Dict = {}
    for d in descriptions:
        classid = d.get("classid")
        instanceid = d.get("instanceid", "0")
        name = d.get("market_name") or d.get("name")
        icon = d.get("icon_url", "")
        if classid and name:
            desc_map[(classid, instanceid)] = {"name": name, "icon": icon}
    items = []
    for a in assets:
        classid = a.get("classid")
        instanceid = a.get("instanceid", "0")
        info = desc_map.get((classid, instanceid)) or desc_map.get((classid, "0")) or {"name": "UNKNOWN_ITEM", "icon": ""}
        items.append(info)
    _inventory_cache[steam_id] = items
    _inventory_cache_time[steam_id] = time.time()
    return items


def load_steam_prices_from_html(html: str) -> List[float]:
    pattern = re.compile(
        r'class="price_flag steam"[^>]*data-price="\$([0-9]+(?:\.[0-9]+)?)"',
        re.IGNORECASE,
    )
    return [float(m.group(1)) for m in pattern.finditer(html)]


def load_moon_prices(text: str) -> Dict[str, float]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    moon_prices: Dict[str, float] = {}
    i = 0
    while i < len(lines):
        if lines[i] == "$" and i + 2 < len(lines):
            try:
                price = float(lines[i + 1].replace(",", "."))
                name = lines[i + 2]
                if not re.match(r"^[\d.,]+$", name) and name != "$":
                    key = normalize_name(name)
                    if key not in moon_prices or price > moon_prices[key]:
                        moon_prices[key] = price
                    i += 3
                    continue
            except ValueError:
                pass
        i += 1
    return moon_prices


def run_analysis(inv_html: str, moon_text: str, min_percent: float, steam_id: str) -> dict:
    inv_items = load_inventory_names(steam_id)
    steam_prices = load_steam_prices_from_html(inv_html)
    n = min(len(inv_items), len(steam_prices))
    inv_items = inv_items[:n]
    steam_prices = steam_prices[:n]

    moon_prices = load_moon_prices(moon_text)

    can_sell_grouped: Dict[str, dict] = {}
    cant_sell_grouped: Dict[str, dict] = {}

    for item, price in zip(inv_items, steam_prices):
        name = item["name"]
        icon = item["icon"]
        key = normalize_name(name)
        moon_price = moon_prices.get(key)
        if moon_price is not None:
            if key not in can_sell_grouped:
                can_sell_grouped[key] = {"name": name, "icon": icon, "count": 1, "moon_unit": moon_price, "steam_sum": price}
            else:
                can_sell_grouped[key]["count"] += 1
                can_sell_grouped[key]["steam_sum"] += price
        else:
            if key not in cant_sell_grouped:
                cant_sell_grouped[key] = {"name": name, "icon": icon, "count": 1, "steam_sum": price}
            else:
                cant_sell_grouped[key]["count"] += 1
                cant_sell_grouped[key]["steam_sum"] += price

    can_sell_list = []
    for e in can_sell_grouped.values():
        count = e["count"]
        moon_unit = e["moon_unit"]
        steam_unit = e["steam_sum"] / count if count > 0 else 0.0
        ratio = (moon_unit / steam_unit * 100) if steam_unit > 0 else 0.0
        can_sell_list.append({
            "name": e["name"], "icon": e.get("icon", ""), "count": count,
            "moon_unit": round(moon_unit, 2),
            "steam_unit": round(steam_unit, 2),
            "ratio": round(ratio, 2),
        })
    can_sell_list.sort(key=lambda x: x["moon_unit"] * x["count"], reverse=True)

    cant_sell_list = []
    for e in cant_sell_grouped.values():
        count = e["count"]
        steam_unit = e["steam_sum"] / count if count > 0 else 0.0
        cant_sell_list.append({
            "name": e["name"], "icon": e.get("icon", ""), "count": count,
            "steam_unit": round(steam_unit, 2),
            "steam_total": round(e["steam_sum"], 2),
        })
    cant_sell_list.sort(key=lambda x: x["steam_total"], reverse=True)

    total_moon_gross = sum(e["moon_unit"] * e["count"] for e in can_sell_list)
    total_moon_net = max(0.0, total_moon_gross * (1 - MOON_FEE_PERCENT) - MOON_FEE_FIXED)
    total_steam_can = sum(e["steam_unit"] * e["count"] for e in can_sell_list)
    can_sell_count = sum(e["count"] for e in can_sell_list)
    cant_sell_count = sum(e["count"] for e in cant_sell_list)
    total_steam_cant = sum(e["steam_total"] for e in cant_sell_list)
    overall_ratio = (total_moon_net / total_steam_can * 100) if total_steam_can > 0 else 0.0

    filtered_list = [e for e in can_sell_list if e["ratio"] >= min_percent] if min_percent > 0 else []
    filtered_moon = sum(e["moon_unit"] * e["count"] for e in filtered_list)
    filtered_steam = sum(e["steam_unit"] * e["count"] for e in filtered_list)
    filtered_count = sum(e["count"] for e in filtered_list)
    filtered_ratio = (filtered_moon / filtered_steam * 100) if filtered_steam > 0 else 0.0

    best_list = sorted(can_sell_list, key=lambda x: x["ratio"], reverse=True)[:20]
    best_moon = sum(e["moon_unit"] * e["count"] for e in best_list)
    best_steam = sum(e["steam_unit"] * e["count"] for e in best_list)
    best_count = sum(e["count"] for e in best_list)
    best_ratio = (best_moon / best_steam * 100) if best_steam > 0 else 0.0

    worst_list = sorted(can_sell_list, key=lambda x: x["ratio"])[:20]
    worst_moon = sum(e["moon_unit"] * e["count"] for e in worst_list)
    worst_steam = sum(e["steam_unit"] * e["count"] for e in worst_list)
    worst_count = sum(e["count"] for e in worst_list)
    worst_ratio = (worst_moon / worst_steam * 100) if worst_steam > 0 else 0.0

    return {
        "can_sell": can_sell_list,
        "cant_sell": cant_sell_list,
        "filtered": filtered_list,
        "best": best_list,
        "worst": worst_list,
        "totals": {
            "can_sell_moon": round(total_moon_net, 2),
            "can_sell_steam": round(total_steam_can, 2),
            "can_sell_ratio": round(overall_ratio, 2),
            "can_sell_count": can_sell_count,
            "cant_sell_steam": round(total_steam_cant, 2),
            "cant_sell_count": cant_sell_count,
            "total_steam_all": round(total_steam_can + total_steam_cant, 2),
            "total_items_all": can_sell_count + cant_sell_count,
            "filtered_moon": round(filtered_moon, 2),
            "filtered_steam": round(filtered_steam, 2),
            "filtered_ratio": round(filtered_ratio, 2),
            "filtered_count": filtered_count,
            "best_moon": round(best_moon, 2),
            "best_steam": round(best_steam, 2),
            "best_ratio": round(best_ratio, 2),
            "best_count": best_count,
            "worst_moon": round(worst_moon, 2),
            "worst_steam": round(worst_steam, 2),
            "worst_ratio": round(worst_ratio, 2),
            "worst_count": worst_count,
        },
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze_route():
    data = request.get_json()
    inv_html = data.get("inv_html", "")
    moon_text = data.get("moon_text", "")
    min_percent = float(data.get("min_percent") or 0)
    steam_id = data.get("steam_id", STEAM_ID)
    try:
        result = run_analysis(inv_html, moon_text, min_percent, steam_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
