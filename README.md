# Grox — private AI developer assistant

Grox is a private Discord assistant for one configured owner. It is not a public client-order or freelance bot.

## What it does

- Accepts project instructions from the owner in the configured Discord channel.
- Asks follow-up questions and prepares a technical specification.
- Generates static websites and Discord bot projects (other bot-platform deployment paths are not enabled yet).
- Runs read-only browser QA for generated websites.
- Can publish websites through Vercel.
- Can publish generated bot projects to GitHub and create Render services.

Website QA checks page loading, HTML structure, JavaScript errors, network failures, and visible controls without clicking buttons or submitting forms. Passing this check is not proof that every application feature or backend integration works.

## Required Render environment variables

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Token of the Grox Discord application |
| `GROX_OWNER_ID` | Numeric Discord user ID of the only person allowed to use Grox |
| `GROQ_API_KEY` | API key used for code generation and requirement analysis |
| `CLIENT_CHANNEL_ID` | Numeric ID of the Discord text channel where the owner starts tasks |
| `GROX_DATABASE_URL` | Dedicated Neon PostgreSQL connection string for Grox's saved tasks and order history |

Do not post tokens or API keys in Discord, GitHub, or this README. Set them only in the Render service's private Environment settings.

## Optional integrations

| Variable | Purpose |
|---|---|
| `VERCEL_TOKEN` | Deploy generated websites |
| `GITHUB_TOKEN` | Write generated project files to GitHub |
| `GITHUB_REPO` | Target repository in `owner/repository` format |
| `GITHUB_BRANCH` | Base branch used to create the generated-projects branch; defaults to `main` |
| `GROX_PROJECTS_BRANCH` | Prefix for isolated per-project branches (for example `generated-projects/order_1001_ab12cd`); defaults to `generated-projects` so one project does not redeploy Grox or other generated services |
| `RENDER_API_KEY` | Create Render services for generated bots |
| `RENDER_OWNER_ID` | Optional explicit Render workspace/owner ID when the API returns multiple owners |
| `GROX_BUILDER_DISCORD_TOKEN` | Discord token injected into a generated Discord bot service |
| `BUILDER_DISCORD_TOKEN` | Legacy fallback for the generated Discord bot token |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Reserved legacy settings; the current automatic deployment flow does not yet publish Telegram bots |

If a required integration is not configured, the corresponding publishing step cannot run.

## Optional behavior settings

- `GROQ_MODEL` — defaults to `openai/gpt-oss-120b`.
- `GROQ_RETRIES` — retry count for temporary Groq failures (default 4).
- `GROQ_TPM_LIMIT` — shared rolling token-per-minute budget (default 8000).
- `GROQ_TPM_RESERVE` — safety margin held back from the TPM budget (default 700).
- `GROQ_MIN_COMPLETION_TOKENS` — minimum completion reservation (default 256).
- `CLIENT_TIMEOUT` — seconds to wait for an owner reply (default 600).
- `MAX_REQUIREMENT_ROUNDS` — maximum clarification rounds (default 3).
- `MAX_TASK_LENGTH` — maximum task/message size (default 12000).
- `MAX_WEBSITE_FIX_ATTEMPTS` — website QA/fix attempts (default 4).
- `POST_DELIVERY_TIMEOUT` — follow-up window in seconds (default 86400).
- `MAX_ACTIVE_ORDERS_PER_USER` — maximum simultaneous tasks (default 3).
- `PORT` — health server port (default 10000).

The `BOT2_*` and `DATABASE_URL` settings are legacy options for the separate Bot 2 deployment flow; they are not Grox's own order database.

## Discord setup

1. Create or open the Grox application in the Discord Developer Portal.
2. Enable the Message Content Intent.
3. Invite the bot to your private server with permission to view the task channel, send messages, create public threads, and send messages in threads.
4. Enable Developer Mode in Discord, copy your own user ID, and set that numeric value as `GROX_OWNER_ID`.
5. Copy the task channel ID into `CLIENT_CHANNEL_ID`.
6. Add the bot token and Groq API key to Render Environment variables.

Grox ignores messages from every Discord account except the configured owner ID.

## Render deployment

- Build command: `pip install -r requirements.txt && playwright install chromium`
- Start command: `python main.py`
- Liveness endpoints: `/` and `/healthz`
- Readiness endpoint: `/readyz`

## Grox data persistence

Grox requires a dedicated `GROX_DATABASE_URL` connection string. The application creates its own `grox_orders` table in that database and saves task details, requirement conversations, status, thread IDs, and deployment metadata. Saved records are restored when Grox starts.

Use a Neon PostgreSQL connection string from the Neon Console. Keep it private and add it only to the Grox Render service's Environment settings. Do not use Bot 2's `DATABASE_URL` or `BOT2_DATABASE_URL` for Grox.

The database preserves task records, order IDs, token reservations, and post-delivery conversation expiry. Active orders use heartbeats so an overlapping Render deploy does not incorrectly interrupt them; truly stale orders are marked `INTERRUPTED` after three minutes. Completed post-delivery conversations resume after a restart while their idle window remains active. A code-generation process interrupted mid-build is not automatically resumed yet. Neon Free also has usage limits, and compute can be suspended when monthly quotas are exhausted.

## Local syntax check

```bash
python -m py_compile main.py
python -m unittest discover -s tests -v
```

The GitHub Actions workflow runs this syntax check on pushes to the Grox branch and pull requests targeting `main`. Passing syntax checks does not replace integration tests against Discord, Groq, Vercel, GitHub, or Render.
