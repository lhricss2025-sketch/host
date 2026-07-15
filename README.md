# SENZO DEV Bot Hosting — Railway Deployment

## Files in this bundle
- `main.py` — the bot itself
- `requirements.txt` — Python dependencies
- `Procfile` — tells Railway how to start the app
- `nixpacks.toml` — makes sure the container has **both Python and Node.js**, since hosted user bots can be `.py` or `.js`

## Steps

1. Push these files to a GitHub repo (or use Railway's "Deploy from local directory" if available).
2. In Railway, create a new project → **Deploy from GitHub repo**.
3. Go to **Variables** and set:

| Variable | Required | Notes |
|---|---|---|
| `BOT_TOKEN` | ✅ yes | From @BotFather |
| `OWNER_ID` | ✅ yes | Your numeric Telegram user ID |
| `ADMIN_ID` | optional | Defaults to OWNER_ID if unset |
| `USERNAME` | optional | e.g. `@yourhandle`, shown as contact |
| `CHANNEL` | optional | Your updates channel link |
| `PORT` | auto | Railway sets this automatically — don't set it yourself |
| `TURSO_URL` | optional | Only if you want a remote Turso database instead of local SQLite |
| `TURSO_TOKEN` | optional | Pairs with `TURSO_URL` |

4. Deploy. Railway will run `nixpacks.toml`'s install step (`pip install -r requirements.txt`), then start with `python main.py`.
5. Once it's live, message your bot on Telegram — send `/start`, then just send a `.zip` or any single file to deploy a hosted bot.

## Notes

- **Persistence:** Railway's filesystem is ephemeral on redeploys unless you attach a volume. If you redeploy, previously-hosted bot *files* (in `upload_bots/`) can be lost unless you mount a Railway Volume at `/app` (or wherever your repo root lands). The points/referrals/bot-metadata database (SQLite by default) has the same caveat — use Turso (`TURSO_URL`/`TURSO_TOKEN`) if you want that to survive redeploys.
- **Node.js bots:** `nixpacks.toml` installs Node 20 alongside Python so uploaded `.js` bots with a `package.json` can `npm install` and run. If you remove `nixpacks.toml`, Railway's default Python-only build won't have `node` available and JS bots will fail with a clear in-chat error instead of crashing silently.
- **1 upload = 1 bot slot:** whether the user sends a single file or a `.zip`, it always counts as exactly one bot slot.
