from __future__ import annotations

import argparse
import datetime as dt
import html as html_module
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


SNAPSHOT_PATH = Path(__file__).parent / "tracker_site" / "data" / "snapshot.json"
ASSETS_PATH = Path(__file__).parent / "tracker_assets.json"
RPC_URL = "https://api.mainnet-beta.solana.com"
BLOCKWORKS_TOKENIZED_EQUITIES_URL = "https://blockworks.com/analytics/spot-dex/spot-dexs-tokenized-equities"
BLOCKWORKS_RESEARCH_API = "https://rest.blockworksresearch.com"
BLOCKWORKS_ISSUER_QUERY_SLUG = "spot-dexs-tokenized-equities-volume-by-issuer"
BLOCKWORKS_LEADERBOARD_QUERY_SLUG = "spot-dexs-tokenized-equity-volume-table"
ISSUER_META = {
    "backpack": {"label": "Backpack", "color": "#e42a2a"},
    "xstocks": {"label": "xStocks", "color": "#43c9b6"},
    "prestocks": {"label": "PreStocks", "color": "#7679f1"},
    "bstocks": {"label": "bStocks", "color": "#dfbf42"},
    "ondo": {"label": "Ondo", "color": "#6b7280"},
    "centrifuge": {"label": "Centrifuge", "color": "#1e6ca0"},
    "backed-finance": {"label": "Backed Finance", "color": "#904ea6"},
    "tessera-labs": {"label": "Tessera Labs", "color": "#ef798a"},
    "remora": {"label": "Remora", "color": "#63b512"},
}


def load_snapshot() -> dict:
    with SNAPSHOT_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_assets() -> dict:
    with ASSETS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_env_file() -> None:
    if load_dotenv:
        load_dotenv()
        return

    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def save_snapshot(snapshot: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_PATH.open("w", encoding="utf-8") as file:
        json.dump(snapshot, file, indent=2, ensure_ascii=True)
        file.write("\n")


def format_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def format_amount(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def request_json(url: str, headers: dict[str, str] | None = None, payload: dict | None = None) -> dict:
    data = None
    method = "GET"
    request_headers = headers or {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
        request_headers = {"Content-Type": "application/json", **request_headers}

    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def request_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def birdeye_token_overview(mint: str, api_key: str) -> dict | None:
    url = "https://public-api.birdeye.so/defi/token_overview?" + urllib.parse.urlencode({"address": mint})
    headers = {
        "X-API-KEY": api_key,
        "x-chain": "solana",
        "accept": "application/json",
    }
    body = request_json(url, headers=headers)
    data = body.get("data")
    return data if isinstance(data, dict) else None


def solscan_holder_count(mint: str, api_key: str) -> int | None:
    url = "https://pro-api.solscan.io/v2.0/token/holders?" + urllib.parse.urlencode(
        {"address": mint, "page": 1, "page_size": 10}
    )
    body = request_json(url, headers={"token": api_key, "accept": "application/json"})
    total = body.get("data", {}).get("total")
    return int(total) if total is not None else None


def jupiter_prices(mints: list[str], api_key: str | None) -> dict:
    url = "https://api.jup.ag/price/v3?" + urllib.parse.urlencode({"ids": ",".join(mints)})
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    return request_json(url, headers=headers)


def jupiter_token_search(mints: list[str]) -> dict[str, dict]:
    url = "https://api.jup.ag/tokens/v2/search?" + urllib.parse.urlencode({"query": ",".join(mints)})
    data = request_json(url, headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"})
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Jupiter token search response")
    return {token["id"]: token for token in data if token.get("id")}


def token_supply(mint: str, helius_key: str | None) -> float | None:
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}" if helius_key else RPC_URL
    body = request_json(
        rpc_url,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint],
        },
    )
    amount = body.get("result", {}).get("value", {}).get("uiAmount")
    return float(amount) if amount is not None else None


def blockworks_execution_id(query_slug: str) -> str:
    html = request_text(BLOCKWORKS_TOKENIZED_EQUITIES_URL)
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
    if not match:
        raise RuntimeError("Blockworks page did not include __NEXT_DATA__")

    data = json.loads(html_module.unescape(match.group(1)))
    visualizations = data["props"]["pageProps"]["content"]["visualizations"]
    for visualization in visualizations:
        if visualization.get("querySlug") == query_slug:
            execution_id = visualization.get("lastExecutionId")
            if execution_id:
                return execution_id
    raise RuntimeError(f"Blockworks visualization was not found: {query_slug}")


def blockworks_latest_issuer_volumes() -> dict:
    execution_id = blockworks_execution_id(BLOCKWORKS_ISSUER_QUERY_SLUG)
    url = f"{BLOCKWORKS_RESEARCH_API}/v1/internal/studio/queries/executions/{execution_id}/rows"
    rows = request_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://blockworks.com",
            "Referer": BLOCKWORKS_TOKENIZED_EQUITIES_URL,
        },
    ).get("data", [])
    if not rows:
        raise RuntimeError("Blockworks issuer rows were empty")

    latest_date = max(row["block_date"][:10] for row in rows)
    by_issuer: dict[str, float] = {}
    for row in rows:
        if row["block_date"][:10] != latest_date:
            continue
        issuer = row["issuer_id"]
        by_issuer[issuer] = by_issuer.get(issuer, 0.0) + float(row["volume_usd"])

    total = sum(by_issuer.values())
    backpack = by_issuer.get("backpack", 0.0)
    cumulative_backpack = sum(
        float(row["volume_usd"])
        for row in rows
        if row.get("issuer_id") == "backpack"
    )
    if total <= 0:
        raise RuntimeError("Blockworks total issuer volume was zero")

    return {
        "date": latest_date,
        "total": total,
        "backpack": backpack,
        "cumulativeBackpack": cumulative_backpack,
        "share": (backpack / total) * 100,
        "issuers": by_issuer,
    }


def blockworks_token_leaderboard() -> dict[str, dict]:
    execution_id = blockworks_execution_id(BLOCKWORKS_LEADERBOARD_QUERY_SLUG)
    url = f"{BLOCKWORKS_RESEARCH_API}/v1/internal/studio/queries/executions/{execution_id}/rows"
    rows = request_json(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://blockworks.com",
            "Referer": BLOCKWORKS_TOKENIZED_EQUITIES_URL,
        },
    ).get("data", [])
    if not rows:
        raise RuntimeError("Blockworks leaderboard rows were empty")

    by_symbol: dict[str, dict] = {}
    for row in rows:
        symbol = row.get("underlying_stock")
        if symbol:
            by_symbol[symbol] = row
    return by_symbol


def first_number(data: dict, keys: list[str]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def apply_live_data(snapshot: dict, assets: dict) -> dict:
    birdeye_key = os.getenv("BIRDEYE_API_KEY", "").strip()
    solscan_key = os.getenv("SOLSCAN_API_KEY", "").strip()
    jupiter_key = os.getenv("JUPITER_API_KEY", "").strip()
    helius_key = os.getenv("HELIUS_API_KEY", "").strip()
    if not any([birdeye_key, solscan_key, jupiter_key, helius_key]):
        raise RuntimeError("No tracker API keys configured")

    now = dt.datetime.now(dt.timezone.utc)
    asset_by_symbol = {asset["symbol"]: asset for asset in assets["tokens"]}
    mints = [asset["mint"] for asset in assets["tokens"]]
    prices = jupiter_prices(mints, jupiter_key or None) if jupiter_key else {}
    try:
        jupiter_tokens = jupiter_token_search(mints)
    except Exception as error:
        print(f"Jupiter token metrics unavailable: {error}")
        jupiter_tokens = {}

    total_volume = 0.0
    total_holders = 0
    for token in snapshot["tokens"]:
        asset = asset_by_symbol[token["symbol"]]
        mint = asset["mint"]
        token["mint"] = mint
        token["name"] = asset["name"]
        token["color"] = asset["color"]

        if birdeye_key:
            overview = birdeye_token_overview(mint, birdeye_key) or {}
            volume = first_number(overview, ["v24hUSD", "volume24hUSD", "volume24h", "trade24h"])
            holders = first_number(overview, ["holder", "holders", "holderCount"])
            price = first_number(overview, ["price", "priceUsd", "value"])
            price_change = first_number(overview, ["priceChange24hPercent", "priceChange24h", "priceChange24hPercent"])

            if volume is not None:
                token["volumeRaw"] = round(volume, 2)
                token["volume24h"] = format_money(volume)
            if holders is not None:
                token["holders"] = f"{int(holders):,}"
            if price is not None:
                token["price"] = format_money(price)
            if price_change is not None:
                token["change24h"] = round(price_change, 1)

        if solscan_key:
            holders = solscan_holder_count(mint, solscan_key)
            if holders is not None:
                token["holders"] = f"{holders:,}"

        if helius_key:
            supply = token_supply(mint, helius_key)
            if supply is not None:
                token["supply"] = format_amount(supply)

        price_data = prices.get(mint, {})
        if price_data:
            if "usdPrice" in price_data:
                token["price"] = format_money(float(price_data["usdPrice"]))
            if "priceChange24h" in price_data:
                token["change24h"] = round(float(price_data["priceChange24h"]), 1)

        token_data = jupiter_tokens.get(mint, {})
        if token_data:
            if "holderCount" in token_data:
                token["holders"] = f"{int(token_data['holderCount']):,}"
                token["holdersRaw"] = int(token_data["holderCount"])
                token["holdersSource"] = "Jupiter"
            if "usdPrice" in token_data:
                token["price"] = format_money(float(token_data["usdPrice"]))
            if "totalSupply" in token_data:
                token["supply"] = format_amount(float(token_data["totalSupply"]))
            stats24h = token_data.get("stats24h") or {}
            if "priceChange" in stats24h:
                token["change24h"] = round(float(stats24h["priceChange"]), 1)
            buy_volume = float(stats24h.get("buyVolume") or 0)
            sell_volume = float(stats24h.get("sellVolume") or 0)
            if buy_volume or sell_volume:
                volume = buy_volume + sell_volume
                token["volumeRaw"] = round(volume, 2)
                token["volume24h"] = format_money(volume)
                token["volumeSource"] = "Jupiter"
                token["volumeStatus"] = "indexed"

        total_volume += float(token.get("volumeRaw", 0))
        total_holders += int(token.get("holdersRaw", str(token.get("holders", "0")).replace(",", "")))

    snapshot["updatedAt"] = now.isoformat().replace("+00:00", "Z")
    snapshot["metrics"]["dailyVolume"]["value"] = round(total_volume, 2)
    snapshot["metrics"]["dailyVolume"]["display"] = format_money(total_volume)
    snapshot["metrics"]["holders"]["value"] = total_holders
    snapshot["metrics"]["holders"]["display"] = f"{total_holders:,}"
    update_daily_volume_from_tokens(snapshot)
    try:
        update_solana_market_share(snapshot)
    except Exception as error:
        print(f"Blockworks market share unavailable: {error}")
        snapshot["metrics"]["solanaShare"]["detail"] = f"{format_money(total_volume)} tracked 24h volume"

    today = now.date().isoformat()
    snapshot["share7d"] = snapshot["share7d"][-6:] + [
        {"date": today, "value": float(snapshot["metrics"]["solanaShare"]["value"])}
    ]
    return snapshot


def update_solana_market_share(snapshot: dict) -> None:
    market = blockworks_latest_issuer_volumes()
    share = round(market["share"], 1)
    issuers = []
    for issuer_id, volume in sorted(market["issuers"].items(), key=lambda item: item[1], reverse=True):
        meta = ISSUER_META.get(issuer_id, {})
        issuers.append(
            {
                "id": issuer_id,
                "label": meta.get("label", issuer_id.replace("-", " ").title()),
                "color": meta.get("color", "#94a3b8"),
                "volume": round(volume, 2),
                "display": format_money(volume),
                "share": round((volume / market["total"]) * 100, 2),
            }
        )

    snapshot["metrics"]["solanaShare"]["label"] = "Backpack share of Solana equity flow"
    snapshot["metrics"]["solanaShare"]["value"] = share
    snapshot["metrics"]["solanaShare"]["display"] = f"{share:.1f}%"
    snapshot["metrics"]["solanaShare"]["detail"] = (
        f"{format_money(market['backpack'])} of {format_money(market['total'])} on {market['date']}"
    )
    snapshot["metrics"]["solanaShare"]["change"] = 0
    snapshot["metrics"]["solanaShare"]["changeSuffix"] = "p"
    snapshot["metrics"]["cumulativeVolume"]["value"] = round(market["cumulativeBackpack"], 2)
    snapshot["metrics"]["cumulativeVolume"]["display"] = format_money(market["cumulativeBackpack"])
    snapshot["metrics"]["cumulativeVolume"]["label"] = "Historical Backpack flow"
    snapshot["metrics"]["cumulativeVolume"]["detail"] = f"Blockworks history through {market['date']}"
    snapshot["marketShare"] = {
        "source": "Blockworks",
        "date": market["date"],
        "total": round(market["total"], 2),
        "totalDisplay": format_money(market["total"]),
        "issuers": issuers,
    }


def update_jupiter_token_metrics(snapshot: dict, assets: dict) -> None:
    asset_by_symbol = {asset["symbol"]: asset for asset in assets["tokens"]}
    mints = [asset["mint"] for asset in assets["tokens"]]
    token_data_by_mint = jupiter_token_search(mints)
    total_holders = 0

    for token in snapshot["tokens"]:
        asset = asset_by_symbol[token["symbol"]]
        mint = asset["mint"]
        token["mint"] = mint
        token["name"] = asset["name"]
        token["color"] = asset["color"]

        token_data = token_data_by_mint.get(mint, {})
        if "holderCount" in token_data:
            token["holders"] = f"{int(token_data['holderCount']):,}"
            token["holdersRaw"] = int(token_data["holderCount"])
            token["holdersSource"] = "Jupiter"
        if "usdPrice" in token_data:
            token["price"] = format_money(float(token_data["usdPrice"]))
        if "totalSupply" in token_data:
            token["supply"] = format_amount(float(token_data["totalSupply"]))

        stats24h = token_data.get("stats24h") or {}
        if "priceChange" in stats24h:
            token["change24h"] = round(float(stats24h["priceChange"]), 1)
        buy_volume = float(stats24h.get("buyVolume") or 0)
        sell_volume = float(stats24h.get("sellVolume") or 0)
        if buy_volume or sell_volume:
            volume = buy_volume + sell_volume
            token["volumeRaw"] = round(volume, 2)
            token["volume24h"] = format_money(volume)
            token["volumeSource"] = "Jupiter"
            token["volumeStatus"] = "indexed"

        total_holders += int(token.get("holdersRaw", str(token.get("holders", "0")).replace(",", "")))

    snapshot["metrics"]["holders"]["value"] = total_holders
    snapshot["metrics"]["holders"]["display"] = f"{total_holders:,}"


def update_backpack_token_volumes(snapshot: dict) -> None:
    leaderboard = blockworks_token_leaderboard()
    total_volume = 0.0

    for token in snapshot["tokens"]:
        row = leaderboard.get(token["symbol"], {})
        issuer = row.get("top_issuer")
        volume_raw = row.get("top_issuer_volume") if issuer == "backpack" else None
        if volume_raw is None:
            token["volumeRaw"] = 0
            token["volume24h"] = "Not indexed"
            token["volumeSource"] = "Blockworks"
            token["volumeStatus"] = "missing"
            token["trades24h"] = row.get("prev_1d_trade_cnt")
            continue

        volume = float(volume_raw)
        token["volumeRaw"] = round(volume, 2)
        token["volume24h"] = format_money(volume)
        token["volumeSource"] = "Blockworks"
        token["volumeStatus"] = "indexed"
        token["trades24h"] = row.get("prev_1d_trade_cnt")
        total_volume += volume

    snapshot["metrics"]["dailyVolume"]["value"] = round(total_volume, 2)
    snapshot["metrics"]["dailyVolume"]["display"] = format_money(total_volume)
    snapshot["metrics"]["dailyVolume"]["label"] = "24H tracked Backpack flow"


def update_daily_volume_from_tokens(snapshot: dict) -> None:
    total_volume = sum(float(token.get("volumeRaw", 0)) for token in snapshot["tokens"])
    snapshot["metrics"]["dailyVolume"]["value"] = round(total_volume, 2)
    snapshot["metrics"]["dailyVolume"]["display"] = format_money(total_volume)
    snapshot["metrics"]["dailyVolume"]["label"] = "24H tracked Backpack flow"


def refresh_with_placeholder_data(snapshot: dict) -> dict:
    """Replace this function with Birdeye/Helius/Solscan API calls."""
    now = dt.datetime.now(dt.timezone.utc)
    rng = random.Random(now.strftime("%Y-%m-%d"))

    total_volume = 0.0
    holders = 0
    for token in snapshot["tokens"]:
        previous = float(token["volumeRaw"])
        volume = max(previous * rng.uniform(0.86, 1.18), 1)
        change = rng.uniform(-1.2, 2.8)
        token["volumeRaw"] = round(volume, 2)
        token["volume24h"] = format_money(volume)
        token["change24h"] = round(change, 1)
        total_volume += volume

        holder_count = int(str(token["holders"]).replace(",", ""))
        holder_count = max(holder_count + rng.randint(-7, 18), 0)
        token["holders"] = f"{holder_count:,}"
        token["holdersChange"] = f"{rng.uniform(-0.8, 1.4):+.1f}%"
        holders += holder_count

    solana_total = max(total_volume / rng.uniform(0.62, 0.82), total_volume)
    share = round((total_volume / solana_total) * 100, 1)

    snapshot["updatedAt"] = now.isoformat().replace("+00:00", "Z")
    snapshot["metrics"]["dailyVolume"]["value"] = round(total_volume, 2)
    snapshot["metrics"]["dailyVolume"]["display"] = format_money(total_volume)
    snapshot["metrics"]["dailyVolume"]["change"] = round(rng.uniform(-1.6, 2.4), 1)
    snapshot["metrics"]["solanaShare"]["value"] = share
    snapshot["metrics"]["solanaShare"]["display"] = f"{share:.1f}%"
    snapshot["metrics"]["solanaShare"]["detail"] = f"{format_money(total_volume)} of {format_money(solana_total)}"
    snapshot["metrics"]["solanaShare"]["change"] = round(rng.uniform(-0.6, 0.6), 2)
    snapshot["metrics"]["holders"]["value"] = holders
    snapshot["metrics"]["holders"]["display"] = f"{holders:,}"

    cumulative = float(snapshot["metrics"]["cumulativeVolume"]["value"]) + total_volume
    snapshot["metrics"]["cumulativeVolume"]["value"] = round(cumulative, 2)
    snapshot["metrics"]["cumulativeVolume"]["display"] = format_money(cumulative)

    today = now.date()
    previous_points = snapshot["share7d"][-6:]
    snapshot["share7d"] = previous_points + [{"date": today.isoformat(), "value": share}]
    return snapshot


def run_once() -> None:
    load_env_file()
    snapshot = load_snapshot()
    assets = load_assets()
    try:
        snapshot = apply_live_data(snapshot, assets)
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
        print(f"Live update unavailable, using placeholder data: {error}")
        for token in snapshot["tokens"]:
            for asset in assets["tokens"]:
                if token["symbol"] == asset["symbol"]:
                    token["mint"] = asset["mint"]
                    token["name"] = asset["name"]
                    token["color"] = asset["color"]
        snapshot = refresh_with_placeholder_data(snapshot)
        try:
            update_solana_market_share(snapshot)
        except Exception as share_error:
            print(f"Blockworks market share unavailable: {share_error}")
        try:
            update_jupiter_token_metrics(snapshot, assets)
            update_daily_volume_from_tokens(snapshot)
        except Exception as token_error:
            print(f"Jupiter token metrics unavailable: {token_error}")
    save_snapshot(snapshot)
    print(f"Updated {SNAPSHOT_PATH}")


def seconds_until_next_run(timezone_name: str, hour: int, minute: int) -> float:
    timezone = ZoneInfo(timezone_name)
    now = dt.datetime.now(timezone)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def run_daily(timezone_name: str, hour: int, minute: int) -> None:
    while True:
        wait_seconds = seconds_until_next_run(timezone_name, hour, minute)
        print(f"Next tracker update in {wait_seconds / 3600:.2f} hours")
        time.sleep(wait_seconds)
        run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="Update the tracker snapshot JSON.")
    parser.add_argument("--once", action="store_true", help="Run one update and exit.")
    parser.add_argument("--daily", action="store_true", help="Run forever, updating once per day.")
    parser.add_argument("--timezone", default="Europe/Rome")
    parser.add_argument("--hour", type=int, default=14)
    parser.add_argument("--minute", type=int, default=30)
    args = parser.parse_args()

    if args.daily:
        run_daily(args.timezone, args.hour, args.minute)
    else:
        run_once()


if __name__ == "__main__":
    main()
