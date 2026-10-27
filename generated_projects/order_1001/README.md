# Bot 2 – Discord community outreach assistant  

A **stand‑alone Discord bot** that can:

* search (via a built‑in static list) for Discord communities in IT, business, startups and gaming,  
* store community data and discovered administrators,  
* generate a personalized outreach message using **Groq** LLM,  
* let an authorized owner review, edit and send the message,  
* record replies and keep the whole workflow in a persistent SQLite database,  
* expose a simple health‑check HTTP endpoint required by Render.

The project is ready to be deployed as a **Render Web Service**.

---  

## Table of Contents  

1. [Features](#features)  
2. [Tech Stack](#tech-stack)  
3. [Setup & Deployment](#setup--deployment)  
4. [Running locally](#running-locally)  
5. [Bot Commands](#bot-commands)  
6. [Environment variables](#environment-variables)  
7. [Database](#database)  
8. [Troubleshooting](#troubleshooting)  

---  

## Features  

| Area | What the bot does |
|------|-------------------|
| **Search** | Loads a pre‑defined list of public Discord servers, stores them in SQLite, avoids duplicates. |
| **Analysis** | Saves topic, member count, source, rules (if known) and whether IT‑service offers are allowed. |
| **Message generation** | Calls Groq’s `mixtral-8x7b-32768` model to create a short, friendly outreach message. |
| **Owner workflow** | Owner (or added admins) can view results, edit drafts, approve and send DMs. |
| **Response tracking** | Replies sent as DMs are captured and linked to the original message. |
| **Persistence** | All data stays in `bot2.db` and survives restarts. |
| **Health endpoint** | `GET /` returns `OK` – required for Render’s web‑service health checks. |

---  

## Tech Stack  

* **Python 3.11**  
* **discord.py** – Discord bot library  
* **aiohttp** – tiny async web server for the health endpoint  
* **httpx** – async HTTP client for Groq API  
* **SQLite** – lightweight persistent storage (standard library)  

---  

## Setup & Deployment  

### 1. Clone the repository  

```bash
git clone https://github.com/your‑org/bot2.git
cd bot2
```

### 2. Create a `.env` file (or set env vars in Render)  

```dotenv
# Required
DISCORD_TOKEN=your-discord-bot-token
GROQ_API_KEY=your-groq-api-key
OWNER_DISCORD_ID=123456789012345678   # Discord user ID of the bot owner
# Optional – comma‑separated list of extra admin IDs
ALLOWED_ADMINS=987654321098765432,112233445566778899
# Render supplies PORT automatically; you can set a default locally
PORT=8080
```

Copy the example file:

```bash
cp .env.example .env
```

### 3. Install dependencies  

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Deploy to Render  

1. Create a **New Web Service** and connect the GitHub repo.  
2. In **Environment**, add the variables from the `.env` file.  
3. Set the **Start Command** to:  

```
python main.py
```

Render will automatically expose the health endpoint on the assigned port.

---  

## Running locally  

```bash
# Ensure environment variables are loaded
export $(cat .env | xargs)

# Start the bot (it also runs the health HTTP server)
python main.py
```

You should see:

```
Logged in as BotName (ID: 123456789012345678)
Health endpoint listening on http://0.0.0.0:8080/
```

Open `http://localhost:8080/` in a browser – it should return `OK`.

---  

## Bot Commands  

All commands are **owner‑only** (or allowed admins) and are invoked with the `!` prefix.

| Command | Usage | Description |
|---------|-------|-------------|
| `!search` | `!search` | Starts the built‑in community search, stores results. |
| `!results [limit]` | `!results 5` | Shows the most recent stored communities (default 10). |
| `!draft <admin_id>` | `!draft 123456789012345678` | Generates (or shows) a message draft for the given admin. |
| `!send <admin_id>` | `!send 123456789012345678` | Sends the stored draft as a DM, records the action. |
| `!responses [limit]` | `!responses 20` | Lists sent messages and any received replies. |
| `!stop` | `!stop` | Cancels a running `!search` operation. |

*The bot will reply in the channel where the command was issued, showing drafts, confirmations and errors.*

---  

## Environment Variables  

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from the Discord developer portal. |
| `GROQ_API_KEY` | ✅ | API key for Groq LLM service. |
| `OWNER_DISCORD_ID` | ✅ | Discord user ID of the bot owner (numeric). |
| `ALLOWED_ADMINS` | ❌ | Comma‑separated list of additional Discord IDs that may use the commands. |
| `PORT` | ✅ (Render) | Port on which the health HTTP server listens (Render sets this automatically). |

If any required variable is missing, the bot aborts with a clear error message.

---  

## Database  

The SQLite file `bot2.db` (created automatically) contains four tables:

* `communities` – stored Discord server information.  
* `admins` – discovered administrators (Discord ID, name, linked community).  
* `drafts` – temporary message drafts awaiting approval.  
* `sent_messages` – history of sent DMs, status, and any admin response.

You can inspect the DB with any SQLite client.

---  

## Troubleshooting  

* **Bot cannot send DMs** – The target user may have privacy settings that block DMs from server‑less bots. The bot will report `🚫 Bot cannot send DM`.  
* **Groq errors** – Ensure `GROQ_API_KEY` is correct and the model name (`mixtral-8x7b-32768`) is available on your plan.  
* **Health check fails on Render** – Verify that the `PORT` env variable is present; the service must listen on that port.  

---  

### License  

This project is provided **as‑is** for the client’s internal use. Feel free to modify or extend it (e.g., add real Discord server discovery, Telegram/Slack integrations, etc.).  

---  

Happy hunting! 🚀