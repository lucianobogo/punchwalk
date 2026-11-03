# Deploying Punchwalk

One Docker image, one Fly machine, one process, one volume at `/data`. Everything is copy-pasteable from
the repo root. Nothing here prints a secret.

## What's in the image

- **Stage `web`** (node 22 + pnpm 9.15): `VITE_DATA=live pnpm -C web build` writes the PWA to
  `punchwalk/static/`. The build fails if a live build would ship `static/mock-api/`.
- **Stage `base`** (python 3.11 on Debian 13 "trixie"):
  - ffmpeg 7.1 (with zscale, for HDR)
  - Pango, HarfBuzz and Cairo, plus DejaVu fonts for WeasyPrint
  - `uv sync --frozen --no-dev` into `/app/.venv`
  - the code, the static build, `scripts/`, `fixtures/cache`, `fixtures/pipeline`, `eval/`, and `demo/` (if it exists), so `scripts/seed.py` (PLAN 8.1) can run inside the container
- **Stage `test`**: `docker build --target test .` runs `tests/test_ingest.py`, `tests/test_report.py` and `tests/test_api_core.py` inside the image. This is the real check that ffmpeg and WeasyPrint work in production.
- **Stage `runtime`** (the default, about 1.1 GB):
  - `scripts/entrypoint.sh` gives `/data` to the `punch` user (uid 1000), then drops root
  - the app runs `uvicorn punchwalk.api.app:app` with one worker. There must be one process, because the 2-job cap and the demo upload limit are per process.
  - `HEALTHCHECK` polls `GET /healthz`
  - environment: `DATA_DIR=/data`, `PUNCHWALK_CACHE=readwrite`, `FIXTURE_CACHE_DIR=/app/fixtures/cache`

## Local run

```bash
make docker                                   # docker build -t punchwalk .
docker build --target test -t punchwalk:test . # in-image tests (~1 min after the first build)
docker run --rm -d -p 8010:8000 -v punchwalk-local:/data --name pw punchwalk
curl -s localhost:8010/healthz                # {"ok":true,...}
curl -s -o /dev/null -w "%{http_code}\n" localhost:8010/   # 200 (the PWA)
docker rm -f pw
```

The container reads no `.env`, because `.dockerignore` keeps secrets out of the image. To try real model
calls locally, pass keys with `--env-file .env`. Every upload then makes live calls, so watch
`data/cost_ledger.jsonl` and the $25 cap.

## Deploy to Fly (one-time setup, then `make deploy`)

Prerequisite: `fly auth whoami` shows your account.

```bash
# 1. The app. If the name is taken, pick another and change `app` and APP_BASE_URL in fly.toml,
#    and the `-a punchwalk` flags below.
fly apps create punchwalk

# 2. The volume (SQLite + uploads + media + cache), in the same region as fly.toml's primary_region.
fly volumes create punchwalk_data --region ewr --size 3 -a punchwalk -y

# 3. Secrets, straight from .env without printing them (only non-empty lines are sent).
grep -E '^(MODEL_API_KEY|BASETEN_API_KEY|SLACK_WEBHOOK_URL|RESEND_API_KEY|EMAIL_FROM)=.+' .env \
  | fly secrets import -a punchwalk
fly secrets list -a punchwalk        # names and digests only

# 4. Build on Fly's builders and start the machine.
fly deploy --remote-only             # later deploys: make deploy (= fly deploy)
```

`fly.toml` keeps one machine always on (`auto_stop_machines = false`, `min_machines_running = 1`), because
the job worker runs inside the web process. It mounts `punchwalk_data` at `/data` and health-checks
`/healthz`. Deploys use `strategy = "immediate"`, since one volume can attach to only one machine. On Fly,
`FLY_APP_NAME` is set, so the demo upload limit keys on `Fly-Client-IP`.

## Smoke test the live URL

```bash
APP=https://punchwalk.fly.dev
curl -s $APP/healthz                                   # {"ok":true,"demo_mode":true,...}
curl -s -o /dev/null -w "%{http_code}\n" $APP/          # 200
curl -s -o /dev/null -w "%{http_code}\n" $APP/mock-api/projects.json   # not JSON (see "Known gaps")

PID=$(curl -s -X POST $APP/api/projects -H 'content-type: application/json' \
  -d '{"name":"Smoke test","rooms":["Kitchen"]}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")

# A 6 s synthetic clip (macOS `say` narration; --tone elsewhere). Processing it makes LIVE model calls
# (a few cents). Skip this block if you only want to check the deploy.
uv run python scripts/synth_clip.py --out .verify/smoke --duration 6 --portrait
WID=$(curl -s -F kind=initial -F clips=@.verify/smoke/walk.mp4 $APP/api/projects/$PID/walks \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['walk_id'])")
for i in $(seq 1 90); do
  S=$(curl -s $APP/api/walks/$WID/progress | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['status'],d['stage'],d['error'] or '')")
  echo "$S"; case "$S" in done*|failed*) break;; esac; sleep 2
done
curl -s -H "Range: bytes=0-99" -o /dev/null -w "%{http_code} %{size_download}\n" $APP/media/walks/$WID/display.mp4  # 206 100
curl -s -o /tmp/punch-report.pdf -w "%{http_code} %{content_type}\n" $APP/api/projects/$PID/report.pdf       # 200 application/pdf
```

Then open `https://punchwalk.fly.dev` on an iPhone (or Playwright's `iPhone 15 Pro` profile) and check the
punch list loads.

## Seed the demo project (PLAN 8.1, once `scripts/seed.py` exists)

`fly ssh console` runs as root. Run scripts as the app user, or the files they write in `/data` (the SQLite
database and its WAL) will be root-owned, and the app can no longer write to them:

```bash
fly ssh console -a punchwalk -C "sh -c 'cd /app && setpriv --reuid=punch --regid=punch --init-groups /app/.venv/bin/python scripts/seed.py'"
```

The seed reads the recorded results from `/app/fixtures/cache` (baked into the image, so it costs nothing)
and media from `/app/demo` or `/app/fixtures`. Rebuild and redeploy after adding demo media.

## Operations

```bash
fly logs -a punchwalk                       # app + job worker logs
fly status -a punchwalk                     # machine, health checks
fly ssh console -a punchwalk                # shell (root; see the seeding note)
fly ssh sftp get /data/cost_ledger.jsonl -a punchwalk   # live spend ledger
fly volumes list -a punchwalk
```

- A restart re-queues interrupted jobs, and the pipeline resumes from the last finished stage.
- Back up: `fly ssh sftp get /data/punchwalk.sqlite3`. Stop traffic first, or copy the `-wal` file too.
- Turn off automatic sending without a rebuild: `fly secrets set AUTO_SEND=0 -a punchwalk`. Setting a secret restarts the machine.

## Known gaps

- `/mock-api/...` returns the PWA's `index.html` (200, `text/html`), because the SPA fallback serves every
  unknown path. No mock data is shipped, and the bundle only calls `/api`. A 404 there needs
  `"mock-api/"` added to `RESERVED_PREFIXES` in `punchwalk/api/media.py`.
- Item numbers in the PDF use DejaVu Sans. Archivo isn't embedded in the PDF; the web app self-hosts it.
