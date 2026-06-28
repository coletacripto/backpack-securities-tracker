import asyncio
import html
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request


load_dotenv()


@dataclass(frozen=True)
class Settings:
    mode: str
    telegram_bot_token: str
    telegram_chat_id: str
    token_mint: str
    token_symbol: str
    usd_threshold: Decimal
    fixed_token_price_usd: Optional[Decimal]
    coingecko_id: str
    webhook_secret: str
    helius_api_key: str
    watch_addresses: tuple[str, ...]
    poll_interval_seconds: int


def decimal_env(name: str, default: Optional[str] = None) -> Optional[Decimal]:
    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise RuntimeError(f"{name} must be a valid decimal number") from exc


def load_settings() -> Settings:
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TOKEN_MINT"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    watch_addresses = tuple(
        address.strip()
        for address in os.getenv("WATCH_ADDRESSES", "").split(",")
        if address.strip()
    )

    return Settings(
        mode=os.getenv("MODE", "webhook").lower(),
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        token_mint=os.environ["TOKEN_MINT"],
        token_symbol=os.getenv("TOKEN_SYMBOL", "TOKEN"),
        usd_threshold=decimal_env("USD_THRESHOLD", "30000") or Decimal("30000"),
        fixed_token_price_usd=decimal_env("FIXED_TOKEN_PRICE_USD"),
        coingecko_id=os.getenv("COINGECKO_ID", "").strip(),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        helius_api_key=os.getenv("HELIUS_API_KEY", ""),
        watch_addresses=watch_addresses,
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "20")),
    )


settings = load_settings()
app = FastAPI(title="Solana Token Telegram Alerts")
seen_signatures: set[str] = set()
cached_price: Optional[tuple[Decimal, float]] = None


async def get_token_price_usd(session: aiohttp.ClientSession) -> Decimal:
    global cached_price

    if settings.fixed_token_price_usd is not None:
        return settings.fixed_token_price_usd

    now = asyncio.get_running_loop().time()
    if cached_price and now - cached_price[1] < 60:
        return cached_price[0]

    jupiter_price = await get_jupiter_price_usd(session)
    if jupiter_price is not None:
        cached_price = (jupiter_price, now)
        return jupiter_price

    if not settings.coingecko_id:
        raise RuntimeError(
            "Could not fetch price from Jupiter. Set FIXED_TOKEN_PRICE_USD or COINGECKO_ID."
        )

    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": settings.coingecko_id, "vs_currencies": "usd"}
    async with session.get(url, params=params, timeout=15) as response:
        response.raise_for_status()
        data = await response.json()

    price = Decimal(str(data[settings.coingecko_id]["usd"]))
    cached_price = (price, now)
    return price


async def get_jupiter_price_usd(session: aiohttp.ClientSession) -> Optional[Decimal]:
    endpoints = (
        "https://lite-api.jup.ag/price/v3",
        "https://api.jup.ag/price/v3",
        "https://price.jup.ag/v6/price",
    )

    for url in endpoints:
        params = {"ids": settings.token_mint}
        try:
            async with session.get(url, params=params, timeout=15) as response:
                if response.status >= 400:
                    continue
                data = await response.json()
        except aiohttp.ClientError:
            continue

        price = extract_jupiter_price(data)
        if price is not None:
            return price

    return None


def extract_jupiter_price(data: dict[str, Any]) -> Optional[Decimal]:
    price_data = data.get(settings.token_mint) or data.get("data", {}).get(settings.token_mint)
    if not isinstance(price_data, dict):
        return None

    value = price_data.get("usdPrice") or price_data.get("price")
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


async def send_telegram(session: aiohttp.ClientSession, text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    async with session.post(url, json=payload, timeout=15) as response:
        response.raise_for_status()


def iter_transactions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("transactions"), list):
            return [item for item in payload["transactions"] if isinstance(item, dict)]
        return [payload]
    return []


def extract_amount(transfer: dict[str, Any]) -> Optional[Decimal]:
    for key in ("tokenAmount", "amount"):
        value = transfer.get(key)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except InvalidOperation:
            continue
    return None


def transfer_matches_token(transfer: dict[str, Any]) -> bool:
    mint = transfer.get("mint") or transfer.get("tokenMint")
    return mint == settings.token_mint


async def handle_transactions(payload: Any) -> int:
    transactions = iter_transactions(payload)
    if not transactions:
        return 0

    alerts_sent = 0
    async with aiohttp.ClientSession() as session:
        price = await get_token_price_usd(session)

        for transaction in transactions:
            signature = transaction.get("signature") or transaction.get("transactionSignature")
            if signature and signature in seen_signatures:
                continue

            token_transfers = transaction.get("tokenTransfers") or []
            for transfer in token_transfers:
                if not isinstance(transfer, dict) or not transfer_matches_token(transfer):
                    continue

                amount = extract_amount(transfer)
                if amount is None:
                    continue

                usd_value = amount * price
                if usd_value < settings.usd_threshold:
                    continue

                if signature:
                    seen_signatures.add(signature)

                await send_telegram(
                    session,
                    format_alert(transaction, transfer, amount, usd_value),
                )
                alerts_sent += 1

    return alerts_sent


def short_address(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 12:
        return str(value or "unknown")
    return f"{value[:6]}...{value[-6:]}"


def format_alert(
    transaction: dict[str, Any],
    transfer: dict[str, Any],
    amount: Decimal,
    usd_value: Decimal,
) -> str:
    signature = transaction.get("signature") or transaction.get("transactionSignature") or ""
    from_account = transfer.get("fromUserAccount") or transfer.get("fromTokenAccount")
    to_account = transfer.get("toUserAccount") or transfer.get("toTokenAccount")
    tx_url = f"https://solscan.io/tx/{signature}" if signature else "N/A"

    return "\n".join(
        [
            "<b>Whale alert on Solana</b>",
            f"Token: <code>{html.escape(settings.token_symbol)}</code>",
            f"Amount: <b>{amount:,.4f}</b>",
            f"Estimated value: <b>${usd_value:,.2f}</b>",
            f"From: <code>{html.escape(short_address(from_account))}</code>",
            f"To: <code>{html.escape(short_address(to_account))}</code>",
            f"Tx: {html.escape(tx_url)}",
        ]
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/helius")
async def helius_webhook(request: Request) -> dict[str, int]:
    if settings.webhook_secret:
        secret = request.query_params.get("secret")
        if secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    payload = await request.json()
    alerts_sent = await handle_transactions(payload)
    return {"alerts_sent": alerts_sent}


async def fetch_signatures(
    session: aiohttp.ClientSession,
    address: str,
    before: Optional[str] = None,
) -> list[dict[str, Any]]:
    url = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
    params: dict[str, Any] = {"api-key": settings.helius_api_key, "limit": 20}
    if before:
        params["before"] = before

    async with session.get(url, params=params, timeout=20) as response:
        response.raise_for_status()
        data = await response.json()
    return data if isinstance(data, list) else []


async def poll_forever() -> None:
    if not settings.helius_api_key:
        raise RuntimeError("HELIUS_API_KEY is required for poll mode")
    if not settings.watch_addresses:
        raise RuntimeError("WATCH_ADDRESSES is required for poll mode")

    print(f"Polling {len(settings.watch_addresses)} address(es) every {settings.poll_interval_seconds}s")

    async with aiohttp.ClientSession() as session:
        while True:
            for address in settings.watch_addresses:
                try:
                    transactions = await fetch_signatures(session, address)
                    await handle_transactions(transactions)
                except Exception as exc:
                    print(f"Polling error for {address}: {exc}")
            await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    if settings.mode != "poll":
        print("Use uvicorn for webhook mode: uvicorn solana_token_telegram_bot:app --host 0.0.0.0 --port 8080")
    else:
        asyncio.run(poll_forever())
