import json
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

import requests

if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)


STEAM_ID = "76561199482415281"
APP_ID = 252490
CONTEXT_ID = 2

# Avan commission: 0.3% + 0.5$ per sale
AVAN_FEE_PERCENT = 0.003
AVAN_FEE_FIXED = 0.5

INV_HTML_PATH = "inv.txt"
AVAN_PATH = "avan.txt"


@dataclass
class InventoryItem:
    name: str
    steam_price: float


def load_inventory_names() -> List[str]:
    """
    Fetch Rust inventory from Steam and return item names
    in the same order as the assets array.
    """
    url = f"https://steamcommunity.com/inventory/{STEAM_ID}/{APP_ID}/{CONTEXT_ID}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()

    assets = data.get("assets", [])
    descriptions = data.get("descriptions", [])

    # Map (classid, instanceid) -> market_name
    desc_map: Dict[Tuple[str, str], str] = {}
    for d in descriptions:
        classid = d.get("classid")
        instanceid = d.get("instanceid", "0")
        name = d.get("market_name") or d.get("name")
        if classid and instanceid and name:
            desc_map[(classid, instanceid)] = name

    names: List[str] = []
    for a in assets:
        classid = a.get("classid")
        instanceid = a.get("instanceid", "0")
        name = desc_map.get((classid, instanceid))
        if name is None:
            # Fallback to non-instance-specific description if present
            name = desc_map.get((classid, "0"))
        if name is None:
            name = "UNKNOWN_ITEM"
        names.append(name)

    return names


def load_steam_prices_from_html(path: str) -> List[float]:
    """
    Parse inv.txt HTML and extract Steam prices in inventory order.

    Prices are in elements like:
      <div class="price_flag steam" data-sort="1" data-price="$4.40"></div>
    """
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    pattern = re.compile(
        r'class="price_flag steam"[^>]*data-price="\$([0-9]+(?:\.[0-9]+)?)"',
        re.IGNORECASE,
    )
    prices: List[float] = []
    for match in pattern.finditer(html):
        value = float(match.group(1))
        prices.append(value)
    return prices


def load_avan_prices(path: str) -> Dict[str, float]:
    """
    Parse avan.txt:
        Name
        99.54 $
        product
    and build mapping name -> best price on Avan.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    avan_prices: Dict[str, float] = {}
    i = 0
    while i + 1 < len(lines):
        name = lines[i]
        price_line = lines[i + 1]

        # Extract number before '$'
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*\$", price_line)
        if not m:
            i += 1
            continue
        price = float(m.group(1))

        key = normalize_name(name)
        current = avan_prices.get(key)
        # Keep the highest Avan price if multiple entries
        if current is None or price > current:
            avan_prices[key] = price

        # Skip to next block: name, price, (optional) 'product'
        i += 1
        if i < len(lines) and lines[i].lower() == "product":
            i += 1

    return avan_prices


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def build_inventory_items() -> List[InventoryItem]:
    names = load_inventory_names()
    steam_prices = load_steam_prices_from_html(INV_HTML_PATH)

    if len(steam_prices) < len(names):
        # Truncate names to match price count to avoid index issues
        names = names[: len(steam_prices)]
    elif len(steam_prices) > len(names):
        steam_prices = steam_prices[: len(names)]

    return [
        InventoryItem(name=n, steam_price=p)
        for n, p in zip(names, steam_prices)
    ]


def main() -> None:
    # Ask user for desired minimum percentage (e.g. 65)
    try:
        desired_percent_str = input("Enter minimum rate (%): ").strip()
        desired_percent = float(desired_percent_str)
    except Exception:
        desired_percent = 0.0

    inventory_items = build_inventory_items()
    avan_prices = load_avan_prices(AVAN_PATH)

    # group by normalized name so duplicates become "Nx item"
    can_sell_grouped: Dict[str, Dict[str, float | int | str]] = {}
    cant_sell_grouped: Dict[str, Dict[str, float | int | str]] = {}
    can_sell_count = 0
    cant_sell_count = 0

    for item in inventory_items:
        key = normalize_name(item.name)
        avan_price = avan_prices.get(key)
        if avan_price is not None:
            can_sell_count += 1
            entry = can_sell_grouped.get(key)
            if entry is None:
                can_sell_grouped[key] = {
                    "name": item.name,
                    "count": 1,
                    "avan_unit": float(avan_price),
                    "steam_sum": float(item.steam_price),
                }
            else:
                entry["count"] = int(entry["count"]) + 1
                entry["steam_sum"] = float(entry["steam_sum"]) + float(item.steam_price)
        else:
            cant_sell_count += 1
            entry = cant_sell_grouped.get(key)
            if entry is None:
                cant_sell_grouped[key] = {
                    "name": item.name,
                    "count": 1,
                    "steam_sum": float(item.steam_price),
                }
            else:
                entry["count"] = int(entry["count"]) + 1
                entry["steam_sum"] = float(entry["steam_sum"]) + float(item.steam_price)

    # Gross and net Avan totals (after commission)
    total_avan_gross = sum(
        float(e["avan_unit"]) * int(e["count"]) for e in can_sell_grouped.values()
    )
    total_avan_net = total_avan_gross * (1 - AVAN_FEE_PERCENT) - AVAN_FEE_FIXED
    if total_avan_net < 0:
        total_avan_net = 0.0

    total_steam_for_can = sum(float(e["steam_sum"]) for e in can_sell_grouped.values())
    total_steam_only = sum(float(e["steam_sum"]) for e in cant_sell_grouped.values())

    # Output
    print("\nItems you can sell on Avan:")
    can_sell_sorted = sorted(can_sell_grouped.values(), key=lambda e: float(e["avan_unit"]) * int(e["count"]), reverse=True)
    for e in can_sell_sorted:
        name = str(e["name"])
        count = int(e["count"])
        avan_unit = float(e["avan_unit"])
        steam_unit = float(e["steam_sum"]) / count if count > 0 else 0.0
        ratio = (avan_unit / steam_unit * 100) if steam_unit > 0 else 0.0
        prefix = f"{count}x " if count > 1 else ""
        print(f"{prefix}{name}: {avan_unit:.2f} / {steam_unit:.2f} = {ratio:.2f}%")
    print("---")
    overall_ratio = (
        (total_avan_net / total_steam_for_can * 100) if total_steam_for_can > 0 else 0.0
    )
    print(
        f"Total: {total_avan_net:.2f} / {total_steam_for_can:.2f} = {overall_ratio:.2f}% / {can_sell_count} items"
    )
    print()

    print("Items you can't sell on Avan:")
    cant_sell_sorted = sorted(cant_sell_grouped.values(), key=lambda e: float(e["steam_sum"]), reverse=True)
    for e in cant_sell_sorted:
        name = str(e["name"])
        count = int(e["count"])
        steam_unit = float(e["steam_sum"]) / count if count > 0 else 0.0
        prefix = f"{count}x " if count > 1 else ""
        print(f"{prefix}{name}: {steam_unit:.2f}")
    print("---")
    print(f"Total: {total_steam_only:.2f} / {cant_sell_count} items\n")

    # Filter items you can sell by desired percent
    if desired_percent > 0 and can_sell_grouped:
        print(f"Items you can sell on Avan with {desired_percent:.2f}% or higher:")
        filtered: List[Tuple[str, int, float, float, float]] = []
        # (name, count, avan_unit, steam_unit, ratio)
        for e in can_sell_grouped.values():
            name = str(e["name"])
            count = int(e["count"])
            avan_unit = float(e["avan_unit"])
            steam_unit = float(e["steam_sum"]) / count if count > 0 else 0.0
            if steam_unit <= 0:
                continue
            ratio = avan_unit / steam_unit * 100
            if ratio >= desired_percent:
                filtered.append((name, count, avan_unit, steam_unit, ratio))

        total_avan_filtered = sum(avan_unit * count for _, count, avan_unit, _, _ in filtered)
        total_steam_filtered = sum(steam_unit * count for _, count, _, steam_unit, _ in filtered)
        filtered_count = sum(count for _, count, _, _, _ in filtered)
        filtered.sort(key=lambda x: x[2] * x[1], reverse=True)
        for name, count, avan_unit, steam_unit, ratio in filtered:
            prefix = f"{count}x " if count > 1 else ""
            print(f"{prefix}{name}: {avan_unit:.2f} / {steam_unit:.2f} = {ratio:.2f}%")
        print("---")
        if total_steam_filtered > 0:
            total_ratio_filtered = total_avan_filtered / total_steam_filtered * 100
        else:
            total_ratio_filtered = 0.0
        print(
            f"Total: {total_avan_filtered:.2f} / {total_steam_filtered:.2f} = {total_ratio_filtered:.2f}% / {filtered_count} items\n"
        )

    # 20 worst items by rate (lowest avan/steam %)
    if can_sell_grouped:
        worst: List[Tuple[str, int, float, float, float]] = []
        for e in can_sell_grouped.values():
            name = str(e["name"])
            count = int(e["count"])
            avan_unit = float(e["avan_unit"])
            steam_unit = float(e["steam_sum"]) / count if count > 0 else 0.0
            if steam_unit <= 0:
                continue
            ratio = avan_unit / steam_unit * 100
            worst.append((name, count, avan_unit, steam_unit, ratio))

        worst.sort(key=lambda x: x[4])  # by ratio asc
        worst = worst[:20]

        print("20 worst items to sell on Avan (lowest rate):")
        worst_avan_total = sum(avan_unit * count for _, count, avan_unit, _, _ in worst)
        worst_steam_total = sum(steam_unit * count for _, count, _, steam_unit, _ in worst)
        worst_count = sum(count for _, count, _, _, _ in worst)

        for name, count, avan_unit, steam_unit, ratio in worst:
            prefix = f"{count}x " if count > 1 else ""
            print(f"{prefix}{name}: {avan_unit:.2f} / {steam_unit:.2f} = {ratio:.2f}%")

        print("---")
        worst_ratio_total = (worst_avan_total / worst_steam_total * 100) if worst_steam_total > 0 else 0.0
        print(
            f"Total: {worst_avan_total:.2f} / {worst_steam_total:.2f} = {worst_ratio_total:.2f}% / {worst_count} items\n"
        )


if __name__ == "__main__":
    main()

