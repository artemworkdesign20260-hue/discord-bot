import os
import asyncio
import logging
import json
import sqlite3
from typing import Optional, List, Tuple

import discord
from discord.ext import commands, tasks

import aiohttp
from aiohttp import web

import httpx

# ----------------------------------------------------------------------
# Configuration & Logging
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Bot2")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OWNER_DISCORD_ID = os.getenv("OWNER_DISCORD_ID")
ALLOWED_ADMINS = os.getenv("ALLOWED_ADMINS", "")

if not DISCORD_TOKEN:
    raise RuntimeError("Environment variable DISCORD_TOKEN is required")
if not GROQ_API_KEY:
    raise RuntimeError("Environment variable GROQ_API_KEY is required")
if not OWNER_DISCORD_ID:
    raise RuntimeError("Environment variable OWNER_DISCORD_ID is required")

OWNER_DISCORD_ID = int(OWNER_DISCORD_ID)
ALLOWED_ADMINS = {int(i) for i in ALLOWED_ADMINS.split(",") if i.strip()}

# ----------------------------------------------------------------------
# Database layer (SQLite)
# ----------------------------------------------------------------------
DB_PATH = "bot2.db"

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    # Communities table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            topic TEXT,
            members INTEGER,
            source TEXT,
            rules TEXT,
            allow_it_offers INTEGER   -- 1 = yes, 0 = no, NULL = unknown
        )
    """)
    # Admins (extracted from communities)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER UNIQUE,
            name TEXT,
            community_id INTEGER,
            extra_contact TEXT,
            FOREIGN KEY (community_id) REFERENCES communities(id)
        )
    """)
    # Draft messages (one per admin)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            admin_id INTEGER PRIMARY KEY,
            content TEXT,
            created_at TEXT,
            FOREIGN KEY (admin_id) REFERENCES admins(discord_id)
        )
    """)
    # Sent messages + status
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            community_id INTEGER,
            content TEXT,
            sent_at TEXT,
            status TEXT,               -- pending / delivered / responded
            response TEXT,
            FOREIGN KEY (admin_id) REFERENCES admins(discord_id),
            FOREIGN KEY (community_id) REFERENCES communities(id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def is_authorized(user_id: int) -> bool:
    return user_id == OWNER_DISCORD_ID or user_id in ALLOWED_ADMINS

async def fetch_groq_response(system_prompt: str, user_prompt: str) -> str:
    """Calls Groq chat completion API and returns the generated text."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "mixtral-8x7b-32768",   # example model, adjust if needed
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

def store_community(link: str, topic: str, members: Optional[int], source: str,
                    rules: Optional[str] = None,
                    allow_it_offers: Optional[int] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO communities (link, topic, members, source, rules, allow_it_offers)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (link, topic, members, source, rules, allow_it_offers))
    conn.commit()
    conn.close()

def fetch_communities(limit: int = 20) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM communities ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def store_admin(discord_id: int, name: str, community_id: int,
                extra_contact: Optional[str] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO admins (discord_id, name, community_id, extra_contact)
        VALUES (?, ?, ?, ?)
    """, (discord_id, name, community_id, extra_contact))
    conn.commit()
    conn.close()

def get_admin(discord_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admins WHERE discord_id = ?", (discord_id,))
    row = cur.fetchone()
    conn.close()
    return row

def save_draft(admin_id: int, content: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO drafts (admin_id, content, created_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(admin_id) DO UPDATE SET content=excluded.content, created_at=datetime('now')
    """, (admin_id, content))
    conn.commit()
    conn.close()

def load_draft(admin_id: int) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT content FROM drafts WHERE admin_id = ?", (admin_id,))
    row = cur.fetchone()
    conn.close()
    return row["content"] if row else None

def record_sent_message(admin_id: int, community_id: int, content: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sent_messages (admin_id, community_id, content, sent_at, status)
        VALUES (?, ?, ?, datetime('now'), 'sent')
    """, (admin_id, community_id, content))
    msg_id = cur.lastrowid
    conn.commit()
    conn.close()
    return msg_id

def update_message_status(msg_id: int, status: str, response: Optional[str] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE sent_messages
        SET status = ?, response = ?
        WHERE id = ?
    """, (status, response, msg_id))
    conn.commit()
    conn.close()

# ----------------------------------------------------------------------
# Discord bot definition
# ----------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Simple in‑memory flag to stop long‑running tasks
stop_requested = False

# ----------------------------------------------------------------------
# Sample static data for “search” (real Discord API does not provide server search)
# ----------------------------------------------------------------------
SAMPLE_COMMUNITIES = [
    {
        "link": "https://discord.gg/itdevs",
        "topic": "IT Development",
        "members": 1200,
        "source": "public-list",
        "rules": "No advertising, IT services allowed",
        "allow_it_offers": 1
    },
    {
        "link": "https://discord.gg/startup-hub",
        "topic": "Startups & Business",
        "members": 800,
        "source": "public-list",
        "rules": "Only business related content, no direct offers",
        "allow_it_offers": 0
    },
    {
        "link": "https://discord.gg/gaming-zone",
        "topic": "Gaming",
        "members": 2000,
        "source": "public-list",
        "rules": None,
        "allow_it_offers": None
    },
    # Add more static entries if desired
]

# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def owner_only():
    async def predicate(ctx):
        if is_authorized(ctx.author.id):
            return True
        await ctx.send("🚫 You are not authorized to use this command.")
        return False
    return commands.check(predicate)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("------")

@bot.command(name="search")
@owner_only()
async def cmd_search(ctx):
    """Launch search for new Discord communities."""
    global stop_requested
    stop_requested = False
    await ctx.send("🔎 Starting search for Discord communities...")
    for entry in SAMPLE_COMMUNITIES:
        if stop_requested:
            await ctx.send("⏹️ Search stopped by user.")
            return
        # Store community (ignore duplicates)
        store_community(
            link=entry["link"],
            topic=entry["topic"],
            members=entry["members"],
            source=entry["source"],
            rules=entry.get("rules"),
            allow_it_offers=entry.get("allow_it_offers")
        )
        # Simulate extracting an admin (in real life we would need to scrape or use API)
        # Here we just create a dummy admin for demonstration.
        dummy_admin_id = 100000000000000000 + entry["members"]  # pseudo‑unique
        store_admin(
            discord_id=dummy_admin_id,
            name=f"Admin_{entry['topic'].replace(' ', '')}",
            community_id=get_community_id_by_link(entry["link"]),
            extra_contact=None
        )
        await asyncio.sleep(0.5)  # simulate network delay
    await ctx.send(f"✅ Search completed. {len(SAMPLE_COMMUNITIES)} communities stored.")

def get_community_id_by_link(link: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM communities WHERE link = ?", (link,))
    row = cur.fetchone()
    conn.close()
    return row["id"] if row else None

@bot.command(name="results")
@owner_only()
async def cmd_results(ctx, limit: int = 10):
    """Show stored search results."""
    rows = fetch_communities(limit)
    if not rows:
        await ctx.send("📂 No communities stored yet.")
        return
    embed = discord.Embed(title="Stored Communities", color=discord.Color.blue())
    for row in rows:
        allow = "✅" if row["allow_it_offers"] == 1 else ("❌" if row["allow_it_offers"] == 0 else "❓")
        embed.add_field(
            name=row["topic"],
            value=(
                f"Link: {row['link']}\n"
                f"Members: {row['members'] or 'N/A'}\n"
                f"IT offers allowed: {allow}\n"
                f"Source: {row['source']}"
            ),
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="draft")
@owner_only()
async def cmd_draft(ctx, admin_id: int):
    """Show or edit a draft message for a given admin."""
    admin = get_admin(admin_id)
    if not admin:
        await ctx.send(f"❌ No admin found with Discord ID `{admin_id}`.")
        return

    # Generate draft if not existent
    existing = load_draft(admin_id)
    if existing:
        await ctx.send(f"📝 Existing draft for **{admin['name']}**:\n```{existing}```")
        return

    # Build prompts for Groq
    system_prompt = "You are a friendly sales representative writing a short, polite message to the admin of a Discord community, offering IT services."
    user_prompt = (
        f"Community topic: {admin['community_id']}\n"
        f"Write a personalized message (max 300 characters) that mentions the community and asks if they are interested in collaboration."
    )
    try:
        generated = await fetch_groq_response(system_prompt, user_prompt)
    except Exception as e:
        logger.exception("Groq API error")
        await ctx.send(f"❗ Failed to generate draft: {e}")
        return

    save_draft(admin_id, generated)
    await ctx.send(f"🆕 Draft created for **{admin['name']}**:\n```{generated}```")

@bot.command(name="send")
@owner_only()
async def cmd_send(ctx, admin_id: int):
    """Send the drafted message to the admin (DM)."""
    admin = get_admin(admin_id)
    if not admin:
        await ctx.send(f"❌ No admin with Discord ID `{admin_id}`.")
        return

    draft = load_draft(admin_id)
    if not draft:
        await ctx.send("⚠️ No draft found. Use `/draft` to create one first.")
        return

    # Find Discord user object (may not be in any shared guild)
    user = bot.get_user(admin_id)
    if not user:
        # Try to fetch by ID (may succeed if the user shares a guild with the bot)
        try:
            user = await bot.fetch_user(admin_id)
        except discord.NotFound:
            user = None

    if not user:
        await ctx.send(f"❌ Could not locate Discord user with ID `{admin_id}`.")
        return

    try:
        await user.send(draft)
    except discord.Forbidden:
        await ctx.send("🚫 Bot cannot send DM to this user (blocked or privacy settings).")
        return
    except Exception as e:
        await ctx.send(f"❗ Failed to send DM: {e}")
        return

    # Record the sent message
    community_id = admin["community_id"]
    msg_record_id = record_sent_message(admin_id, community_id, draft)

    # Clear draft
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM drafts WHERE admin_id = ?", (admin_id,))
    conn.commit()
    conn.close()

    await ctx.send(f"✅ Message sent to **{admin['name']}** (Discord ID: `{admin_id}`). Record ID: `{msg_record_id}`")

@bot.command(name="responses")
@owner_only()
async def cmd_responses(ctx, limit: int = 20):
    """Show recent responses from admins."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sm.id, a.name, a.discord_id, sm.content, sm.status, sm.response, sm.sent_at
        FROM sent_messages sm
        LEFT JOIN admins a ON sm.admin_id = a.discord_id
        ORDER BY sm.sent_at DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await ctx.send("📭 No messages have been sent yet.")
        return

    embed = discord.Embed(title="Sent Messages & Responses", color=discord.Color.green())
    for row in rows:
        embed.add_field(
            name=f"To: {row['name']} ({row['discord_id']})",
            value=(
                f"Message ID: {row['id']}\n"
                f"Status: {row['status']}\n"
                f"Sent at: {row['sent_at']}\n"
                f"Response: {row['response'] or '—'}"
            ),
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="stop")
@owner_only()
async def cmd_stop(ctx):
    """Stop the currently running long‑running process (search)."""
    global stop_requested
    stop_requested = True
    await ctx.send("🛑 Stop request received. Ongoing operation will halt shortly.")

# ----------------------------------------------------------------------
# Capture replies from admins (DMs) and store them
# ----------------------------------------------------------------------
@bot.event
async def on_message(message):
    # Allow commands to be processed
    await bot.process_commands(message)

    # We only care about DMs from stored admins
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        admin_row = get_admin(message.author.id)
        if not admin_row:
            return  # Not a tracked admin

        # Find the latest sent message to this admin that is still pending
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM sent_messages
            WHERE admin_id = ? AND status = 'sent'
            ORDER BY sent_at DESC LIMIT 1
        """, (message.author.id,))
        row = cur.fetchone()
        conn.close()

        if row:
            # Update status and store response
            update_message_status(row["id"], "responded", message.content)
            logger.info(f"Received response from admin {message.author.id}: {message.content}")
        else:
            # No pending message – just log it
            logger.info(f"Unrelated DM from admin {message.author.id}: {message.content}")

# ----------------------------------------------------------------------
# Simple health HTTP endpoint (Render expects it)
# ----------------------------------------------------------------------
async def health(request):
    return web.Response(text="OK", status=200)

def run_web_app():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    return runner

# ----------------------------------------------------------------------
# Main entry point – run both bot and web server concurrently
# ----------------------------------------------------------------------
async def main():
    # Start health endpoint
    port = int(os.getenv("PORT", "8080"))
    runner = await run_web_app()
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health endpoint listening on http://0.0.0.0:{port}/")

    # Start Discord bot
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user.")