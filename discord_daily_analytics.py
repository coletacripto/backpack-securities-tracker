from __future__ import annotations

import asyncio
import datetime as dt
import os
import sqlite3
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv


DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: str
    report_channel_id: str
    timezone: str
    database_path: str


class DiscordClient:
    def __init__(self, token: str):
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "discord-daily-analytics/1.0",
            }
        )

    async def close(self) -> None:
        await self.session.close()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{DISCORD_API_BASE}{path}"
        while True:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status == 429:
                    data = await response.json()
                    await asyncio.sleep(float(data.get("retry_after", 1)))
                    continue

                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"Discord API error {response.status} {method} {path}: {body}")

                if response.status == 204:
                    return None

                return await response.json()

    async def get_guild(self, guild_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/guilds/{guild_id}?with_counts=true")

    async def get_channels(self, guild_id: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/guilds/{guild_id}/channels")

    async def get_members(self, guild_id: str) -> list[dict[str, Any]]:
        members: list[dict[str, Any]] = []
        after = "0"

        while True:
            page = await self.request(
                "GET",
                f"/guilds/{guild_id}/members",
                params={"limit": 1000, "after": after},
            )
            if not page:
                break

            members.extend(page)
            after = page[-1]["user"]["id"]

            if len(page) < 1000:
                break

        return members

    async def get_messages_for_day(
        self,
        channel_id: str,
        start_utc: dt.datetime,
        end_utc: dt.datetime,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        before = snowflake_from_datetime(end_utc)

        while True:
            page = await self.request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": 100, "before": before},
            )
            if not page:
                break

            stop = False
            for message in page:
                created_at = parse_discord_datetime(message["timestamp"])
                if created_at < start_utc:
                    stop = True
                    continue
                if start_utc <= created_at < end_utc:
                    messages.append(message)

            if stop or len(page) < 100:
                break

            before = page[-1]["id"]

        return messages

    async def send_report(self, channel_id: str, payload: dict[str, Any]) -> None:
        await self.request("POST", f"/channels/{channel_id}/messages", json=payload)


class ChannelAccessError(RuntimeError):
    pass


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "1097564246277099571").strip()
    report_channel_id = os.getenv("DISCORD_REPORT_CHANNEL_ID", "1518633466261016748").strip()
    timezone = os.getenv("REPORT_TIMEZONE", "Europe/Rome").strip()
    database_path = os.getenv("DISCORD_ANALYTICS_DB", "discord_analytics.db").strip()

    missing = []
    if not token:
        missing.append("DISCORD_TOKEN")
    if not guild_id:
        missing.append("DISCORD_GUILD_ID")
    if not report_channel_id:
        missing.append("DISCORD_REPORT_CHANNEL_ID")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        token=token,
        guild_id=guild_id,
        report_channel_id=report_channel_id,
        timezone=timezone,
        database_path=database_path,
    )


def parse_discord_datetime(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def snowflake_from_datetime(value: dt.datetime) -> int:
    timestamp_ms = int(value.timestamp() * 1000)
    return (timestamp_ms - DISCORD_EPOCH_MS) << 22


def report_window(timezone_name: str) -> tuple[dt.date, dt.datetime, dt.datetime]:
    timezone = ZoneInfo(timezone_name)
    today = dt.datetime.now(timezone).date()
    report_date = today - dt.timedelta(days=1)
    start_local = dt.datetime.combine(report_date, dt.time.min, timezone)
    end_local = start_local + dt.timedelta(days=1)
    return report_date, start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS member_snapshots (
            snapshot_date TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            joined_at TEXT,
            PRIMARY KEY (snapshot_date, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            report_date TEXT PRIMARY KEY,
            total_messages INTEGER NOT NULL,
            new_members INTEGER NOT NULL,
            left_members INTEGER NOT NULL,
            total_members INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def previous_snapshot_date(conn: sqlite3.Connection, report_date: dt.date) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(snapshot_date)
        FROM member_snapshots
        WHERE snapshot_date < ?
        """,
        (report_date.isoformat(),),
    ).fetchone()
    return row[0] if row and row[0] else None


def snapshot_user_ids(conn: sqlite3.Connection, snapshot_date: str) -> set[str]:
    rows = conn.execute(
        "SELECT user_id FROM member_snapshots WHERE snapshot_date = ?",
        (snapshot_date,),
    ).fetchall()
    return {row[0] for row in rows}


def save_snapshot(conn: sqlite3.Connection, report_date: dt.date, members: list[dict[str, Any]]) -> None:
    snapshot_date = report_date.isoformat()
    conn.execute("DELETE FROM member_snapshots WHERE snapshot_date = ?", (snapshot_date,))
    conn.executemany(
        """
        INSERT INTO member_snapshots (snapshot_date, user_id, username, joined_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                snapshot_date,
                member["user"]["id"],
                member["user"].get("global_name") or member["user"].get("username") or "unknown",
                member.get("joined_at"),
            )
            for member in members
        ],
    )
    conn.commit()


def count_new_members(
    members: list[dict[str, Any]],
    start_utc: dt.datetime,
    end_utc: dt.datetime,
) -> int:
    count = 0
    for member in members:
        joined_at_raw = member.get("joined_at")
        if not joined_at_raw:
            continue
        joined_at = parse_discord_datetime(joined_at_raw)
        if start_utc <= joined_at < end_utc:
            count += 1
    return count


def text_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supported_types = {0, 5}
    return sorted(
        [channel for channel in channels if channel.get("type") in supported_types],
        key=lambda channel: (channel.get("position", 0), channel.get("name", "")),
    )


def build_report_payload(
    guild: dict[str, Any],
    report_date: dt.date,
    channel_counts: list[tuple[str, str, int]],
    new_members: int,
    left_members: int | None,
    total_members: int,
    timezone_name: str,
) -> dict[str, Any]:
    total_messages = sum(count for _, _, count in channel_counts)
    top_channels = sorted(channel_counts, key=lambda item: item[2], reverse=True)
    active_channels = sum(1 for _, _, count in channel_counts if count > 0)

    left_text = str(left_members) if left_members is not None else "initial snapshot"
    net_growth = None if left_members is None else new_members - left_members

    fields = [
        {"name": "Total messages", "value": str(total_messages), "inline": True},
        {"name": "New members", "value": str(new_members), "inline": True},
        {"name": "Members left", "value": left_text, "inline": True},
        {"name": "Total members", "value": str(total_members), "inline": True},
        {"name": "Active channels", "value": f"{active_channels}/{len(channel_counts)}", "inline": True},
    ]
    if net_growth is not None:
        fields.append({"name": "Net growth", "value": f"{net_growth:+d}", "inline": True})

    fields.extend(channel_report_fields(top_channels))

    return {
        "embeds": [
            {
                "title": f"Daily report - {report_date.strftime('%Y-%m-%d')}",
                "description": f"Server: {guild.get('name', 'Discord')} | Timezone: {timezone_name}",
                "color": 0x5865F2,
                "fields": fields,
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        ]
    }


def channel_report_fields(channel_counts: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    if not channel_counts:
        return [
            {
                "name": "Messages by channel",
                "value": "No accessible text channels were found.",
                "inline": False,
            }
        ]

    visible_channels = [
        (channel_id, name, count)
        for channel_id, name, count in channel_counts
        if count > 5
    ]
    if not visible_channels:
        return [
            {
                "name": "Messages by channel",
                "value": "No channels had more than 5 messages.",
                "inline": False,
            }
        ]

    lines = [f"`{count:>5}` <#{channel_id}> ({name})" for channel_id, name, count in visible_channels]

    fields: list[dict[str, Any]] = []
    current: list[str] = []
    current_length = 0

    for line in lines:
        line_length = len(line) + 1
        if current and current_length + line_length > 950:
            fields.append(
                {
                    "name": "Messages by channel" if not fields else "Messages by channel, continued",
                    "value": "\n".join(current),
                    "inline": False,
                }
            )
            current = []
            current_length = 0

        current.append(line)
        current_length += line_length

    if current:
        fields.append(
            {
                "name": "Messages by channel" if not fields else "Messages by channel, continued",
                "value": "\n".join(current),
                "inline": False,
            }
        )

    return fields[:20]


async def run() -> None:
    settings = load_settings()
    report_date, start_utc, end_utc = report_window(settings.timezone)
    client = DiscordClient(settings.token)

    try:
        guild, channels, members = await asyncio.gather(
            client.get_guild(settings.guild_id),
            client.get_channels(settings.guild_id),
            client.get_members(settings.guild_id),
        )

        channel_counts: list[tuple[str, str, int]] = []
        inaccessible_channels: list[str] = []
        for channel in text_channels(channels):
            try:
                messages = await client.get_messages_for_day(channel["id"], start_utc, end_utc)
                channel_counts.append((channel["id"], channel["name"], len(messages)))
            except RuntimeError as exc:
                if "Missing Access" in str(exc):
                    inaccessible_channels.append(f"#{channel.get('name', channel['id'])}")
                    continue
                raise

        conn = init_db(settings.database_path)
        previous_date = previous_snapshot_date(conn, report_date)
        current_ids = {member["user"]["id"] for member in members}
        left_members: int | None = None

        if previous_date:
            previous_ids = snapshot_user_ids(conn, previous_date)
            left_members = len(previous_ids - current_ids)

        new_members = count_new_members(members, start_utc, end_utc)
        total_messages = sum(count for _, _, count in channel_counts)

        save_snapshot(conn, report_date, members)
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_reports
                (report_date, total_messages, new_members, left_members, total_members, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                report_date.isoformat(),
                total_messages,
                new_members,
                left_members if left_members is not None else 0,
                len(members),
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        payload = build_report_payload(
            guild=guild,
            report_date=report_date,
            channel_counts=channel_counts,
            new_members=new_members,
            left_members=left_members,
            total_members=len(members),
            timezone_name=settings.timezone,
        )
        try:
            await client.send_report(settings.report_channel_id, payload)
        except RuntimeError as exc:
            if "Missing Access" in str(exc):
                raise ChannelAccessError(
                    "The bot cannot access the report channel. "
                    "Grant View Channel, Send Messages, Embed Links, and Read Message History "
                    f"for channel {settings.report_channel_id}."
                ) from exc
            raise

        print(f"Report sent to {settings.report_channel_id}.")
        print(f"Messages: {total_messages} | New members: {new_members} | Members left: {left_members}")
        if inaccessible_channels:
            print(f"Channels skipped due to missing access: {len(inaccessible_channels)}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
