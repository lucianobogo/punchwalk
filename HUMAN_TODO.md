# Your checklist

Everything Claude Code can't do for you, in order. Deadline: **Wed Nov 4, 9:00 PM ET.**

## 1. Before the run (~90 min)

### Tools (10 min)
- Update Claude Code: `claude update`, then check `claude --version`. Fable 5.1 needs **v2.1.257 or later**.
- Install anything missing: Python 3.11+, uv, Node 22 (or 20.17+), pnpm, ffmpeg, flyctl, gh.
  - macOS: `brew install uv node pnpm ffmpeg flyctl gh`

### Accounts and keys (15 min)
- **Meta Model API:** claim the $150 credits (form in the hackathon guide), add a card, and create an API key → `MODEL_API_KEY`.
- **Baseten:** create an account, add a card, and create an API key → `BASETEN_API_KEY`. Also submit the hackathon credit form.
- **Slack (recommended):** create a free workspace and add an Incoming Webhook to a channel such as `#punch-electrical` → `SLACK_WEBHOOK_URL`. This makes dispatch real on camera.
- **Fly.io (recommended):** run `fly auth login` so the run can deploy overnight.
- **GitHub:** create a **private** repo named `punchwalk`. Make it public right before you submit.

### Repo (5 min)
```bash
mkdir punchwalk && cd punchwalk
unzip ~/Downloads/punchwalk-starter.zip      # puts every file at the repo root
chmod +x .claude/hooks/*.sh scripts/*.sh
cp .env.example .env                          # then fill in the keys
git init && git add -A && git commit -m "starter: brief, design and agent setup"
gh repo create punchwalk --private --source . --push
```

### Footage and labels (60 min)
Follow FOOTAGE.md. Videos go in `footage/`, label CSVs in `eval/labels/`. Commit the labels (the videos are gitignored).

## 2. Start the run

```bash
./scripts/orchestrate.sh
```

1. If Claude Code asks whether to trust the folder, say yes. The hooks only run in trusted folders.
2. If a Fable usage-credits prompt appears, accept it. Fable can bill to usage credits instead of your plan's included limits.
3. Paste this as your first message. It starts the work and keeps it going until it's done:

```
/goal Build Punchwalk by running PLAN.md under your orchestrator instructions until every PLAN.md box is [x] or [-] and verified. Prove it in your final message by running and showing: grep -c '^- \[ \]' PLAN.md (prints 0), the tail of make test (all passing), the make eval summary (targets in PLAN.md 3.4 met), and STATE.md showing "Status: COMPLETE". The goal is also met if TZ=America/New_York date shows it is past Wed Nov 4 2026 2:00 PM ET and STATE.md shows "Status: FROZEN" with a final handoff. Never stop to ask me anything: log what only a human can do under "Needs human" in STATE.md and keep working on everything else.
```

4. Watch the first 15–20 minutes. Phase 0 makes the first live API calls, so key or account problems show up there.

**Good to know:**
- **Permission mode.** The script starts in auto mode, where a classifier approves routine actions. If auto mode pauses after repeated blocks, approve the prompt and it carries on. For a fully hands-off night you can run `PUNCHWALK_PERMISSION_MODE=bypassPermissions ./scripts/orchestrate.sh`, but only on a machine or user account with nothing else important on it.
- **Keep the laptop plugged in with the lid open.** The script uses `caffeinate`, so the Mac won't sleep.
- **If `/goal` isn't available** (old version, or hooks disabled), paste this instead: `Begin the build. Follow your orchestrator instructions and keep going until PLAN.md is done or the freeze time.`

## 3. Check-ins

- **~7 PM:** skim STATE.md (Status, Health, Needs human).
- **Before bed:** do everything under "Needs human". Make sure `fly auth whoami` works and the Slack webhook is in `.env`.
- **Wed 8 AM:** open the live URL on your iPhone and run one fresh upload end to end.
- **Wed 12 PM:** code freeze.

## 4. If it stops

- **Same session:** `./scripts/orchestrate.sh --continue`. An active `/goal` comes back by itself.
- **Fresh session:** `./scripts/orchestrate.sh`, then paste the `/goal` line again. The startup hook loads STATE.md, so it continues where it left off.
- **Fable credits running low:** `PUNCHWALK_MODEL=claude-opus-5-5 ./scripts/orchestrate.sh --continue`. If the session still shows Fable, type `/model claude-opus-5-5`.
- **Check progress any time:** `cat STATE.md`, `tail PROGRESS.md`, `git log --oneline`.

## 5. Wednesday afternoon: the video (12–4 PM)

1. **Install the app.** On your iPhone, open the live URL in Safari → Share → Add to Home Screen. Open it from the Home Screen so there's no Safari bar.
2. **Prepare the phone.** Turn on Do Not Disturb and charge to 100%.
3. **Record** with iOS Screen Recording, following `docs/DEMO_SCRIPT.md`:
   1. Record → upload the glasses walk
   2. processing checklist
   3. punch list, filters, Play this moment
   4. walk detail
   5. Trades → send (show the Slack message arriving)
   6. re-walk summary
   7. How this walk was processed
4. **Edit to 3:00 or less** with a voiceover:
   - glasses footage full screen for the hook
   - the phone recording centered on a plain background
   - a short caption naming each Meta model as it appears
5. **Cut a 30–45 s vertical version for X.**

## 6. Submit (by 7 PM)

1. Make the GitHub repo public. Check that the README's "Built during the hackathon" section is there.
2. Upload the video (YouTube unlisted or similar) and post the short cut on X.
3. Submit on the Cerebral Valley platform with:
   - Startup track, theme "Build for Your World"
   - repo URL, video URL, X post link, live URL
   - a note that all reasoning runs on Muse Spark Open via Baseten (for the open-weights prize)
4. Ask friends to vote in the community gallery.
