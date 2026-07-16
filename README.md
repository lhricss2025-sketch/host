# SENZO DEV Bot Hosting — Railway Deployment

## Files in this bundle
- `main.py` — the bot itself
- `requirements.txt` — Python dependencies
- `Dockerfile` — **use this.** Guarantees both Python and Node.js are installed, regardless of what Railway's auto-builder decides to do.
- `Procfile` / `nixpacks.toml` — kept as a fallback only. If Railway is set to build with Nixpacks instead of your Dockerfile, `nixpacks.toml` should also install Node — but in testing this was unreliable on some Railway projects (Node ended up missing). **The Dockerfile fixes this for good**, so prefer it.

## Steps

1. Push these files to a GitHub repo (all of them, including `Dockerfile`).
2. In Railway, create a new project → **Deploy from GitHub repo**.
3. Go to your service → **Settings → Build** → confirm the **Builder** is set to `Dockerfile` (Railway usually auto-detects this when a `Dockerfile` is present at the repo root; if it still shows Nixpacks, switch it manually).
4. Go to **Variables** and set:

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

5. Deploy. Watch the build logs — you should see `apt-get install nodejs` run during the image build. If you don't see that step at all, Railway is still using Nixpacks instead of the Dockerfile — go back to step 3.
6. Once it's live, message your bot on Telegram — send `/start`, then just send a `.zip` or any single file to deploy a hosted bot.

## How to confirm Node is really there

After deploying, upload any small `.js` bot with a trivial `package.json` (no dependencies) and check that it runs instead of showing the "needs Node.js" message. If it still shows that message, Node truly isn't installed in the running container — double check the build logs for the `nodejs` install step.

## Notes

- **Persistence:** Railway's filesystem is ephemeral on redeploys unless you attach a volume. If you redeploy, previously-hosted bot *files* (in `upload_bots/`) can be lost unless you mount a Railway Volume at `/app` (or wherever your repo root lands). The points/referrals/bot-metadata database (SQLite by default) has the same caveat — use Turso (`TURSO_URL`/`TURSO_TOKEN`) if you want that to survive redeploys.
- **1 upload = 1 bot slot:** whether the user sends a single file or a `.zip`, it always counts as exactly one bot slot.
