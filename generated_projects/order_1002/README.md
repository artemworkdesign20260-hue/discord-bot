# Community Scout – Discord Bot

A Discord bot that can search (simulated) public Discord communities, store their metadata in PostgreSQL and expose results via slash‑commands.  
It also runs a tiny HTTP health‑check endpoint required by Render.

## Features

| Feature | Description |
|---------|-------------|
| **/search** | Start a new search (owner only). |
| **/results** | Paginated view of the latest search results. |
| **/export** | Export results as CSV or JSON. |
| **/stop** | Cancel an ongoing search. |
| **/help** | Show help (available to everyone). |
| **Health endpoint** | `GET /` returns `OK` (port taken from `PORT`). |

## Prerequisites

* Python 3.11+
* PostgreSQL database (Render, Supabase, etc.)

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Bot token from the Discord developer portal. |
| `OWNER_DISCORD_ID` | Yes | Discord user ID of the bot owner (numeric). |
| `DATABASE_URL` | Yes | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/db`. |
| `PORT` | No (default **8000**) | Port for the HTTP health endpoint (Render expects this). |

## Setup & Run

```bash
# 1️⃣ Clone the repo
git clone <repo-url>
cd <repo-directory>

# 2️⃣ Install dependencies
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3️⃣ Set environment variables (example)
export DISCORD_TOKEN="YOUR_BOT_TOKEN"
export OWNER_DISCORD_ID="123456789012345678"
export DATABASE_URL="postgresql://user:pass@host:5432/db"
export PORT=8000   # optional

# 4️⃣ Run the bot
python main.py
```

The bot will:

1. Connect to PostgreSQL and ensure required tables exist.  
2. Start an aiohttp web server exposing `GET /` (returns `OK`).  
3. Log in to Discord and register slash‑commands.

## Usage (owner only)

* `/search keywords:<text> [min_members] [region] [platform] [limit] [language]` – begins a mock search.  
* `/results page:<int>` – view results, 10 per page.  
* `/export format:<CSV|JSON>` – download all results.  
* `/stop` – abort a running search.  
* `/help` – shows the command list (any user).

## Notes

* The current implementation **simulates** a search and generates dummy data; replace `simulate_search` with real scraping logic if needed.  
* No messages are sent to server administrators – only metadata is stored.  
* All secrets are read from environment variables; never commit them to source control.  

---  

© 2026 Grox Solutions – Senior Software Developer.