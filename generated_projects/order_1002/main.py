import os
import asyncio
import logging
import json
import csv
import io
import random
from datetime import datetime, timezone
from typing import Optional, List, Dict

import discord
from discord import app_commands, Interaction, Embed, File
from discord.ext import commands

import asyncpg
from aiohttp import web

# ------------------------------------------------------------
# Configuration & Logging
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("community_scout")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_DISCORD_ID = os.getenv("OWNER_DISCORD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8000"))

if not DISCORD_TOKEN:
    log.error("Missing DISCORD_TOKEN environment variable")
    raise SystemExit(1)
if not OWNER_DISCORD_ID:
    log.error("Missing OWNER_DISCORD_ID environment variable")
    raise SystemExit(1)
if not DATABASE_URL:
    log.error("Missing DATABASE_URL environment variable")
    raise SystemExit(1)

OWNER_DISCORD_ID = int(OWNER_DISCORD_ID)

# ------------------------------------------------------------
# Database utilities
# ------------------------------------------------------------
class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(dsn=self.dsn)
        await self._ensure_tables()
        log.info("Database connected and tables ensured")

    async def close(self):
        if self.pool:
            await self.pool.close()
            log.info("Database pool closed")

    async def _ensure_tables(self):
        """Create tables if they do not exist."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_jobs (
                    id                BIGSERIAL PRIMARY KEY,
                    owner_id          BIGINT NOT NULL,
                    keywords          TEXT[] NOT NULL,
                    min_members       INTEGER NOT NULL,
                    region            TEXT NOT NULL,
                    platform          TEXT NOT NULL,
                    limit_val         INTEGER NOT NULL,
                    language          TEXT,
                    status            TEXT NOT NULL,
                    started_at        TIMESTAMPTZ NOT NULL,
                    finished_at       TIMESTAMPTZ
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guilds (
                    id           BIGINT PRIMARY KEY,
                    invite_url   TEXT UNIQUE,
                    name         TEXT,
                    topic        TEXT,
                    member_count INTEGER,
                    region       TEXT,
                    language     TEXT,
                    source       TEXT,
                    search_job_id BIGINT REFERENCES search_jobs(id) ON DELETE CASCADE,
                    created_at   TIMESTAMPTZ NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contacts (
                    id          BIGSERIAL PRIMARY KEY,
                    guild_id    BIGINT REFERENCES guilds(id) ON DELETE CASCADE,
                    type        TEXT,
                    value       TEXT,
                    source_url  TEXT,
                    found_at    TIMESTAMPTZ NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TYPE IF NOT EXISTS guild_status_enum AS ENUM (
                    'NEW',
                    'PENDING_REVIEW',
                    'VERIFIED',
                    'REJECTED',
                    'CONTACT_FOUND',
                    'CONTACT_NOT_FOUND',
                    'APPROVED_FOR_CONTACT',
                    'CONTACTED',
                    'REPLIED',
                    'NO_RESPONSE'
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_statuses (
                    id        BIGSERIAL PRIMARY KEY,
                    guild_id  BIGINT REFERENCES guilds(id) ON DELETE CASCADE,
                    status    guild_status_enum NOT NULL,
                    changed_at TIMESTAMPTZ NOT NULL,
                    reason    TEXT
                );
                """
            )

    # ---------- Search Jobs ----------
    async def create_search_job(self, owner_id: int, keywords: List[str], min_members: int,
                                region: str, platform: str, limit_val: int,
                                language: Optional[str]) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO search_jobs (owner_id, keywords, min_members, region,
                                         platform, limit_val, language, status,
                                         started_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,'RUNNING', $8)
                RETURNING id;
                """,
                owner_id,
                keywords,
                min_members,
                region,
                platform,
                limit_val,
                language,
                datetime.now(timezone.utc),
            )
            return row["id"]

    async def update_search_job_status(self, job_id: int, status: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE search_jobs
                SET status = $1,
                    finished_at = CASE WHEN $1 IN ('FINISHED','STOPPED','FAILED') THEN $2 ELSE NULL END
                WHERE id = $3;
                """,
                status,
                datetime.now(timezone.utc),
                job_id,
            )

    async def get_latest_job(self, owner_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM search_jobs
                WHERE owner_id = $1
                ORDER BY started_at DESC
                LIMIT 1;
                """,
                owner_id,
            )
            return row

    # ---------- Guild ----------
    async def insert_guild(self, guild_data: dict):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guilds (id, invite_url, name, topic, member_count,
                                    region, language, source, search_job_id, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (id) DO NOTHING;
                """,
                guild_data["id"],
                guild_data["invite_url"],
                guild_data["name"],
                guild_data["topic"],
                guild_data["member_count"],
                guild_data["region"],
                guild_data["language"],
                guild_data["source"],
                guild_data["search_job_id"],
                datetime.now(timezone.utc),
            )
            # Insert status entry
            await conn.execute(
                """
                INSERT INTO guild_statuses (guild_id, status, changed_at)
                VALUES ($1, $2, $3);
                """,
                guild_data["id"],
                "NEW",
                datetime.now(timezone.utc),
            )

    async def fetch_guilds_by_job(self, job_id: int, offset: int, limit: int) -> List[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT g.*, gs.status
                FROM guilds g
                LEFT JOIN LATERAL (
                    SELECT status FROM guild_statuses
                    WHERE guild_id = g.id
                    ORDER BY changed_at DESC
                    LIMIT 1
                ) gs ON true
                WHERE g.search_job_id = $1
                ORDER BY g.created_at ASC
                OFFSET $2 LIMIT $3;
                """,
                job_id,
                offset,
                limit,
            )
            return rows

    async def fetch_all_guilds_by_job(self, job_id: int) -> List[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT g.*, gs.status
                FROM guilds g
                LEFT JOIN LATERAL (
                    SELECT status FROM guild_statuses
                    WHERE guild_id = g.id
                    ORDER BY changed_at DESC
                    LIMIT 1
                ) gs ON true
                WHERE g.search_job_id = $1
                ORDER BY g.created_at ASC;
                """,
                job_id,
            )
            return rows

# ------------------------------------------------------------
# Discord Bot
# ------------------------------------------------------------
intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database(DATABASE_URL)

# Keep reference to the currently running search task per owner
active_search_tasks: Dict[int, asyncio.Task] = {}

# ------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------
def is_owner(inter: Interaction) -> bool:
    return inter.user.id == OWNER_DISCORD_ID

def build_guild_embed(guild_record: asyncpg.Record) -> Embed:
    embed = Embed(title=guild_record["name"] or "Unnamed Server", color=0x00ff00)
    embed.add_field(name="Guild ID", value=str(guild_record["id"]), inline=False)
    embed.add_field(name="Invite", value=guild_record["invite_url"] or "N/A", inline=False)
    embed.add_field(name="Members", value=str(guild_record["member_count"] or "N/A"), inline=True)
    embed.add_field(name="Region", value=guild_record["region"] or "N/A", inline=True)
    embed.add_field(name="Language", value=guild_record["language"] or "N/A", inline=True)
    embed.add_field(name="Status", value=guild_record["status"] or "UNKNOWN", inline=False)
    embed.set_footer(text=f"Source: {guild_record['source']}")
    return embed

async def simulate_search(inter: Interaction, job_id: int,
                          keywords: List[str],
                          min_members: int,
                          region: str,
                          platform: str,
                          limit: int,
                          language: Optional[str]):
    """A very simple mock search that creates dummy guilds."""
    try:
        for i in range(1, limit + 1):
            # Check if the task has been cancelled
            if inter.user.id not in active_search_tasks:
                await inter.edit_original_response(
                    embed=Embed(title="Search stopped", description="The search was cancelled.", color=0xff0000)
                )
                await db.update_search_job_status(job_id, "STOPPED")
                return

            # Create dummy data
            guild_id = random.randint(100000000000000000, 999999999999999999)
            invite_url = f"https://discord.gg/fake{i}"
            name = f"{' '.join(keywords).title()} Server {i}"
            topic = f"A community about {' '.join(keywords)}."
            member_count = random.randint(min_members, min_members + 5000)
            guild_data = {
                "id": guild_id,
                "invite_url": invite_url,
                "name": name,
                "topic": topic,
                "member_count": member_count,
                "region": region,
                "language": language,
                "source": "MockGenerator",
                "search_job_id": job_id,
            }
            await db.insert_guild(guild_data)

            # Update progress message every 10 inserts
            if i % 10 == 0 or i == limit:
                await inter.edit_original_response(
                    embed=Embed(
                        title="Search in progress",
                        description=f"Found **{i}** of **{limit}** servers.",
                        color=0x00ff00,
                    )
                )
            await asyncio.sleep(0.1)  # simulate network latency

        await db.update_search_job_status(job_id, "FINISHED")
        await inter.edit_original_response(
            embed=Embed(title="Search completed", description=f"Found **{limit}** servers.", color=0x00ff00)
        )
    except Exception as e:
        log.exception("Error during simulated search")
        await db.update_search_job_status(job_id, "FAILED")
        await inter.edit_original_response(
            embed=Embed(title="Search failed", description=str(e), color=0xff0000)
        )
    finally:
        # Clean up the active task reference
        active_search_tasks.pop(OWNER_DISCORD_ID, None)

# ------------------------------------------------------------
# Bot Events & Commands
# ------------------------------------------------------------
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    log.info("Slash commands synced.")

# /help command (available to everyone)
@bot.tree.command(name="help", description="Show available commands")
async def help_cmd(inter: Interaction):
    embed = Embed(title="Community Scout Bot - Commands", color=0x3498db)
    embed.add_field(name="/search", value="Start a new search (owner only).", inline=False)
    embed.add_field(name="/results", value="Show paginated results of the last search (owner only).", inline=False)
    embed.add_field(name="/export", value="Export results as CSV or JSON (owner only).", inline=False)
    embed.add_field(name="/stop", value="Stop an active search (owner only).", inline=False)
    embed.add_field(name="/help", value="Show this help message.", inline=False)
    await inter.response.send_message(embed=embed, ephemeral=True)

# /search command
@bot.tree.command(name="search", description="Search public Discord communities")
@app_commands.describe(
    keywords="Space separated keywords",
    min_members="Minimum number of members (default 500)",
    region="Region filter (EU, US, WORLD, etc.)",
    platform="Platform (Discord, Telegram, Slack)",
    limit="Maximum number of servers to fetch",
    language="Optional language filter"
)
async def search_cmd(
    inter: Interaction,
    keywords: str,
    min_members: int = 500,
    region: str = "EU",
    platform: str = "Discord",
    limit: int = 100,
    language: Optional[str] = None,
):
    if not is_owner(inter):
        await inter.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    await inter.response.defer()
    kw_list = keywords.split()
    job_id = await db.create_search_job(
        owner_id=OWNER_DISCORD_ID,
        keywords=kw_list,
        min_members=min_members,
        region=region,
        platform=platform,
        limit_val=limit,
        language=language,
    )
    # Register the task
    task = asyncio.create_task(
        simulate_search(
            inter,
            job_id,
            kw_list,
            min_members,
            region,
            platform,
            limit,
            language,
        )
    )
    active_search_tasks[OWNER_DISCORD_ID] = task

# /results command
@bot.tree.command(name="results", description="Show search results (paginated)")
@app_commands.describe(page="Page number (starting from 1)")
async def results_cmd(inter: Interaction, page: int = 1):
    if not is_owner(inter):
        await inter.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    await inter.response.defer()
    job = await db.get_latest_job(OWNER_DISCORD_ID)
    if not job:
        await inter.followup.send("No search job found.", ephemeral=True)
        return

    per_page = 10
    offset = (page - 1) * per_page
    records = await db.fetch_guilds_by_job(job["id"], offset, per_page)

    if not records:
        await inter.followup.send("No results on this page.", ephemeral=True)
        return

    embed = Embed(title=f"Search Results – Page {page}", color=0x1abc9c)
    for rec in records:
        name = rec["name"] or "Unnamed"
        value = (
            f"ID: {rec['id']}\n"
            f"Members: {rec['member_count']}\n"
            f"Region: {rec['region']}\n"
            f"Status: {rec['status']}"
        )
        embed.add_field(name=name, value=value, inline=False)

    await inter.followup.send(embed=embed, ephemeral=True)

# /export command
@bot.tree.command(name="export", description="Export search results")
@app_commands.describe(format="File format")
async def export_cmd(inter: Interaction, format: str = "CSV"):
    if not is_owner(inter):
        await inter.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    await inter.response.defer()
    job = await db.get_latest_job(OWNER_DISCORD_ID)
    if not job:
        await inter.followup.send("No search job found.", ephemeral=True)
        return

    records = await db.fetch_all_guilds_by_job(job["id"])

    if format.upper() == "JSON":
        data = [
            {
                "guild_id": rec["id"],
                "name": rec["name"],
                "invite_url": rec["invite_url"],
                "topic": rec["topic"],
                "member_count": rec["member_count"],
                "region": rec["region"],
                "language": rec["language"],
                "status": rec["status"],
            }
            for rec in records
        ]
        file_bytes = io.BytesIO(json.dumps(data, indent=2).encode())
        file = File(fp=file_bytes, filename="results.json")
        await inter.followup.send("Here is the JSON export:", file=file, ephemeral=True)
    else:  # CSV
        output = io.StringIO()
        writer = csv.writer(output)
        header = [
            "guild_id", "name", "invite_url", "topic",
            "member_count", "region", "language", "status"
        ]
        writer.writerow(header)
        for rec in records:
            writer.writerow([
                rec["id"],
                rec["name"],
                rec["invite_url"],
                rec["topic"],
                rec["member_count"],
                rec["region"],
                rec["language"],
                rec["status"],
            ])
        file_bytes = io.BytesIO(output.getvalue().encode())
        file = File(fp=file_bytes, filename="results.csv")
        await inter.followup.send("Here is the CSV export:", file=file, ephemeral=True)

# /stop command
@bot.tree.command(name="stop", description="Stop an active search")
async def stop_cmd(inter: Interaction):
    if not is_owner(inter):
        await inter.response.send_message("You are not authorized to use this command.", ephemeral=True)
        return

    task = active_search_tasks.pop(OWNER_DISCORD_ID, None)
    if not task:
        await inter.response.send_message("No active search to stop.", ephemeral=True)
        return

    task.cancel()
    await inter.response.send_message("Search cancellation requested.", ephemeral=True)

# ------------------------------------------------------------
# Health endpoint (aiohttp)
# ------------------------------------------------------------
async def handle_health(request):
    return web.Response(text="OK")

async def start_web_app():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Health endpoint listening on port {PORT}")

# ------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------
async def main():
    await db.connect()
    await start_web_app()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")