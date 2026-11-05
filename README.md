# Punchwalk

**Walk it once. Get a punch list with proof.** Punchwalk turns a construction punch walk into a verified punch list,
sent to each trade.

An inspector walks the unit wearing Ray-Ban Meta glasses (or holding a phone) and says what's wrong.
That's all an item needs. Pointing is optional: when a defect is subtle, or the exact spot matters, hold a
finger near it for about two seconds and Punchwalk uses the fingertip to pin it down. Punchwalk turns that
one video into a punch list. Each item has a photo with the defect outlined (plus a dot where the finger
pointed, when it did), a plain summary, the action needed, the trade, the room and the severity. An outline
is drawn only when it passes a check, because no outline beats a wrong one. Every item is checked against
its own photo and the inspector's words, then sent to the trade that has to fix it. On the re-walk, Punchwalk signs off what's
fixed, with before and after photos, and flags anything "marked fixed, but the defect is still visible".

*The voice says what. SAM proves where. Spark Open writes it up and checks it.*

Built from scratch for the Meta Global AI Developer Hackathon, starting Tue Nov 3, 2026 at 3:06 PM ET (first commit) — see the commit history.

- **Live app:** https://punchwalk.fly.dev. On a phone it opens the demo project "Unit 2B final punch" (three real walks,
  14 items); on a desktop, the landing page with the live app in a phone frame and a QR code.
- **Built for:** Meta Global AI Developer Hackathon, Startup track, Nov 3–4, 2026
- **Meta models:** Muse Voice Transcribe (narration), SAM 3.1 (hand tracking and defect outlines), Muse Spark Open on Baseten (every LLM call: writing, grounding, verification).
- **Run it locally:** `make setup && cp .env.example .env && make dev`, or `make test` offline. See [Run it](#run-it).

| Punch list | Item detail | Play this moment | Walk detail | Re-walk summary | How it was processed |
|---|---|---|---|---|---|
| ![Punch list](docs/screens/01-punch-list.png) | ![Item detail](docs/screens/02-item-detail.png) | ![Play this moment](docs/screens/03-play-this-moment.png) | ![Walk detail](docs/screens/04-walk-detail.png) | ![Re-walk summary](docs/screens/06-rewalk-summary.png) | ![How it was processed](docs/screens/07-how-processed.png) |

<sub>iPhone 15 Pro (393 × 852), Playwright WebKit, captured from the current build serving the demo project
after it was re-grounded with grounding v2 (the data the live demo snapshot is exported from). These are real: our own
glasses footage, and the quotes, red defect outlines and blue "pointed here" dots the pipeline produced. Also in the screens folder: the message preview for a trade
([09](docs/screens/09-trade-preview.png)), the desktop landing page ([08](docs/screens/08-landing-desktop.png)),
and the record checklist mid-run ([05](docs/screens/05-record-processing-mock.png)). That one is from
mock data, because no walk was processing when we captured. See [the screenshots README](docs/screens/README.md).</sub>

### What to look at in the demo project

- **#003** (stovetop): the red outline sits on the burnt ring of the front-left burner (the first version
  outlined the whole cooktop). Marked fixed on the re-walk, but the defect is still visible, so it's held as
  "Needs a look" with before and after photos. **#012** (hallway pencil marks) is the second false claim.
- **#004** (light switch screw): the third "Needs a look". It was marked fixed on the re-walk, but the
  photo doesn't clearly show it, so a person decides.
- **#011** (hallway outlet): tap **Play this moment** to jump to the second it was called out.
- **Walks → First walk**: items slide in over the video as the inspector calls them out.
- **Walks → Re-walk**: "7 signed off. 2 still open. 3 need a look."
- **Trades → Brightline Painting**: "1 to review, 2 to send." The message preview, then **Post 2 items to
  Slack**.
- **First walk → How this walk was processed**: every model call, by model, in order.

## Why

A final punch walk takes minutes. The write-up takes hours: photos sorted by hand, defects retyped from
notes, a spreadsheet per trade. The contractor's final payment waits on it. Project managers do this on
their phones, between sites. Punchwalk makes the walk itself the input, and every item carries its proof:
the exact second of video, the photo with the defect outlined, and the inspector's own words.

**Beyond punch lists.** The same loop works anywhere someone walks a space and says what's wrong:
construction site and safety walks, Airbnb and rental turnovers and cleaning checks, move-in and move-out
reports in property management, and facilities and QA walkthroughs. What changes is the vocabulary and
who fixes what. The voice, the visual proof, the write-up and the check stay the same.

## How it works

```mermaid
flowchart TD
    A["Ray-Ban Meta glasses or phone<br/>inspector says what is wrong<br/>(points ~2 s when it is subtle)"] --> B["Upload: one or more clips, in order<br/>(PWA, streamed to disk)"]
    B --> C["FastAPI + in-process job worker<br/>(SQLite, 2 jobs at a time)"]
    C --> D["Ingest (ffmpeg)<br/>display.mp4 (HDR tone-mapped to SDR),<br/>10 fps sam.mp4, 16 kHz audio"]
    D --> E["Muse Voice Transcribe<br/>turns with timestamps"]
    D --> F["SAM 3.1 video, concept 'hand'<br/>hand tracked at 10 fps"]
    E --> G["Muse Spark Open on Baseten<br/>sort each line: room, defect, status update"]
    F --> H["Pointing events<br/>hand held still, fingertip from the mask"]
    G --> I["Fuse: each defect mention + its pointing event, if any"]
    H --> I
    I --> J["Evidence frame<br/>pointed: the pointing moment, hand out of the way<br/>otherwise, or if that frame misses the defect:<br/>Muse Spark Open on Baseten picks the best of<br/>up to 8 sharp, hand-free frames from the item's window"]
    J --> K["Muse Spark Open on Baseten<br/>writes the item: summary, action, trade, room, severity"]
    K --> L1["Muse Spark Open on Baseten<br/>locates the defect itself on a labelled grid"]
    L1 --> L2["SAM 3.1 image, one multi-object request<br/>box and point prompts (plus one concept request)"]
    L2 --> L3["Score each candidate mask: area, IoU with the box,<br/>fingertip, shape, plain-surface and hand rules<br/>Muse Spark Open checks the best ones zoomed in, then smooth<br/>nothing passes: no outline, only the dot if pointed"]
    L3 --> M["Muse Spark Open on Baseten<br/>verifies the item against photo + quote"]
    M --> N["Send: the trade's Slack, email, the team Slack or Copy link<br/>verified items go out, flagged ones wait"]
    N --> O["Phone PWA: punch list, evidence, Play this moment,<br/>Trades (To review, To send, Sent), PDF report"]
    O -. "re-walk (same route)" .-> P["Muse Spark Open on Baseten<br/>maps each status update to an open item<br/>and compares before vs after"]
    P --> Q["Signed off / still open (sent again) /<br/>needs a look: marked fixed, still visible"]
    Q --> O
```

Every stage is idempotent, cached, timed and resumable, and every external call is logged as a
`ModelCall` row with latency and estimated cost. That log is what the "How this walk was processed"
screen shows.

### Each Meta model and exactly what it does

| Model | Where | What it does in Punchwalk |
|---|---|---|
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | Meta Model API, `/v1/asr/transcribe` | Transcribes the walk's audio in `ENDPOINTING` mode: one turn per sentence, with start and end times. A built-in construction vocabulary ("strike plate", "escutcheon", "GWB", room names) plus the project's keywords bias recognition. The turn times pick the moment each item's photo comes from, match a sentence to a pointing event when there is one, and set where "Play this moment" starts. |
| **SAM 3.1**, video (`sam-3.1`) | Meta Model API, `/v1/responses`, streamed | Tracks the concept "hand" through the walk at 10 fps, one request per uploaded clip. When the inspector points, SAM 3.1 follows the hand: a hand that holds still during a defect mention becomes a pointing event, and the fingertip comes from the hand mask. The walk view draws the hand outline from these masks. Walks without pointing still work; their items come from the voice alone. |
| **SAM 3.1**, image | same endpoint, image input | Outlines the defect on the evidence frame (grounding v2). One request per item carries every candidate as its own prompted object, built with `meta_sam_parser.build_object_prompt` in the image's pixels: Spark Open's box; the box plus the fingertip; the fingertip alone; the fingertip with negative points just outside the box (an unpointed item gets the box, and the box with negative points). Small boxes run on a zoomed crop and are mapped back. A second request tries a 1–3-word concept for the defect itself ("burn mark"). Punchwalk scores what comes back and keeps an outline only if it passes the score and Spark Open's check; otherwise the item keeps the "pointed here" dot, or no mark if nobody pointed. |
| **Muse Spark Open** on Baseten (`meta-models/muse-spark-open-1026`) | Baseten, OpenAI-compatible chat completions, strict `json_schema` | All the reasoning. It sorts each transcript line (room, defect, status update) and writes each item from the evidence frame plus the quote (title, summary, action, trade, room, severity; `reasoning_effort=low`). For items nobody pointed at, or whose frame doesn't show the defect, it picks the evidence frame from up to 8 candidates. It locates the defect itself on a gridded copy of the frame, then checks the best candidate outlines zoomed in and picks one or none. It verifies each item against its rendered evidence and the original quote (`medium`). On a re-walk, it maps each status update to an open item and compares the before and after crops. |
| **Muse Spark 1.3** (`muse-spark-1.3`) | Meta Model API | **Fallback only.** It is used if Baseten fails twice (5xx or timeout) on a call. Every fallback is logged with `provider = "meta-fallback"` and shown in the UI. |

### Open weights

Every LLM call in the product path goes to **Muse Spark Open, Meta's open-weights model, served by
Baseten**, under the hackathon slug `meta-models/muse-spark-open-1026`. To be precise about what happened
on Nov 3: that slug returned `HTTP 404 {"error":"please check the model you provided"}`, while the catalog
slug `meta-models/muse-spark-open` answered. The client tries `-1026` first, falls back to the catalog slug
on a 404, remembers which slug answered, and records it on every call (`fixtures/probe/README.md`). Both
slugs are the same open-weights model on Baseten. The hosted Muse Spark 1.3 is reached only through the
logged fallback above, and `REASONING_PROVIDER=meta` is the explicit switch to use it instead. In the
demo project, all 108 reasoning calls (including the grounding v2 locate, frame-pick and check calls) went
to Muse Spark Open on Baseten, and none fell back. The only Muse Spark 1.3 call in the whole build was one
probe call that tested the fallback's schema.

### Say it; point when it's subtle

The inspector doesn't stage anything, and doesn't have to point. Saying what's wrong while looking at it
is enough: the frame picker finds a sharp, hand-free frame that shows the defect from the moment it was
described, Spark Open locates the defect on it, and SAM 3.1 outlines it. Pointing is for subtle defects (a
hairline crack, a small nick) or when the exact spot matters. Hold an index finger close to the defect for
about two seconds. SAM 3.1 follows the hand, and the fingertip pins the location and guides the outline.
An item is never sent to "Needs a look" just because nobody pointed.

### How the outline is drawn (grounding v2)

The first version asked SAM 3.1 for the object the defect is on, and often outlined the whole fixture:
the whole cooktop for a burn mark, the whole switch plate for a missing screw. Grounding v2 is the
production default (`GROUNDING_V2=1` on Fly):

1. **Locate.** Muse Spark Open looks at the evidence frame with a labelled pixel grid drawn on it (and the
   fingertip as a small ring, if the inspector pointed). It returns a tight box around the defect itself,
   how big the defect is (a spot, a region, a whole surface), a 1–3-word concept, and whether the defect is
   visible at all. If it isn't, the frame picker tries other frames from the item's window.
2. **Segment.** One multi-object SAM 3.1 image request with box and point prompts, plus one concept request.
3. **Score.** Each candidate mask is scored on its area against the box and the defect's size, IoU with the
   located box, distance to the fingertip, shape (a solid, compact mask beats a thin arc or a sliver), and two
   rules: a mask on a plain stretch of wall that looks like its surroundings is rejected, and so is one on
   the inspector's hand.
4. **Check.** The best candidates are drawn, numbered, on zoomed-in crops. Muse Spark Open picks the one
   that tightly covers the described defect, or none.
5. **Smooth.** The picked mask is upsampled to the frame, blurred and thresholded, its holes filled, and
   simplified into a clean SVG path.

**No outline beats a wrong outline.** If nothing passes the score and the check, the item keeps only the
blue "pointed here" dot (when the inspector pointed) or no mark at all (voice-only). Ray-Ban Meta footage is
HDR (HLG, BT.2020); ingest tone-maps it to SDR (BT.2408 reference white, hable, BT.1886) so the evidence
frames aren't washed out.

### Sending to trades

The Trades tab uses one vocabulary for every trade: **To review** (needs a look before it can go out),
**To send** (open, never sent) and **Sent** (sent on any channel, including a copied link). Each send uses
the trade's effective channel, and the app names it everywhere: the trade's own Slack channel, else email
if email is configured, else the team's Slack channel, else Copy link. Buttons say what happens ("Post 2
items to Slack", "Email 2 items to Brightline Painting", "Copy a link for 2 items"), and the history says
what happened ("Posted 2 items to #punch-electrical", "Emailed 2 items to Brightline Painting", "Copied a
link for 2 items"). A flagged item goes out only after a person taps "Approve and send to <company>". In the
demo project, Brightline Painting has #009 and #010 under To send and posts to the team's Slack channel.

## Evaluation

The inspector labels the staged defects for each walk (`eval/labels/*.csv`). `make eval` matches
predicted items to labels: within 6 s, and the same room or similar words, with a Hungarian assignment.
It writes `eval/report.md`, and it runs entirely from the cache. walk3 is a holdout in a different space
and is never used for tuning. `make eval` replays the recorded model answers of the first grounding
version (v1), so the table below is the recorded v1 run and grounding v2 doesn't change it. The outline
quality numbers are the blind ratings in the next section.

| Metric | Target | walk1 (first walk) | walk2 (re-walk) | walk3 (holdout) |
|---|---|---|---|---|
| Defects found (recall) | ≥ 0.90 | **1.00** (12/12) | n/a | **1.00** (2/2) |
| Precision | ≥ 0.90 | **1.00** (12/12) | n/a | **1.00** (2/2) |
| Right trade | ≥ 0.85 | **1.00** (12/12) | n/a | **1.00** (2/2) |
| Right room | ≥ 0.90 | **1.00** (12/12) | n/a | **1.00** (2/2) |
| Defect outlined (pointed items) | reported | 0.91 (10/11) | n/a | 0.50 (1/2) |
| Needs a look rate | reported | 0.25 (3/12) | n/a | 0.50 (1/2) |
| Closure accuracy (signed off vs. really fixed) | ≥ 0.90 | n/a | **0.92** (11/12) | n/a |
| False "fixed" claims caught | 100% | n/a | **100%** (2/2) | n/a |

Every target we set before tuning is met. The full per-item tables are in [eval/report.md](eval/report.md). Read
these numbers with their limits in mind:

- **It's a small test set.** walk1 has 12 labeled defects, walk2 has 12 re-walk statements (two of them
  false "fixed" claims), and walk3 has 2 defects. One miss would move walk1's recall by 8 points and
  walk3's by 50. Treat this as a careful smoke test, not a benchmark.
- **walk1 and walk2 were tuned on.** The prompt and threshold changes are in the git history (commit
  "phase 3.4: tuning on walk1+walk2"). walk3 was recorded in a laundry room and never used for tuning.
- **Our walks point at most defects** (11 of 12 on walk1), so the outline rate is reported for pointed
  items. Described-only items are scored the same way for found, trade and room. "Defect outlined" only
  counts whether an outline was drawn, not whether it was right; the blind ratings below measure that.
- **The "Needs a look" items in the eval run** (walk1: #001, #005, #008; walk3: #013) are photo-quality
  flags from the recorded v1 run. Their frames were overexposed or blurry, so the verifier said "The frame
  doesn't show the defect described." and held them for a person instead of sending them. All four items
  are correct, and the three walk1 items were signed off on the re-walk.
- **The demo project as it is now** (re-grounded with v2) has 3 "Needs a look" items, all from the
  re-walk: #003 and #012 ("Marked fixed on the re-walk, but the defect is still visible.") and #004
  ("Marked fixed on the re-walk, but the photo doesn't clearly show it."). The frame picker gave #013 a
  frame that shows the mold, so it is Open and waiting to be sent.
- **The one closure miss is the safe kind.** #004 (light switch screw) really was fixed, but the re-walk
  photo didn't clearly show it, so it went to "Needs a look" instead of being signed off. Nothing was
  signed off that wasn't fixed.
- **Model answers vary from run to run.** These numbers replay the recorded answers of the final run
  (`make eval`, cache readonly, $0). A fresh live run can come out differently.

### Outline quality: blind ratings

An independent rater looked at contact sheets of the 14 demo items' evidence frames with their outlines,
without knowing which version made them, and rated each outline:

- **correct**: the red outline tightly covers the described defect itself
- **partial**: it covers the defect but also a lot that isn't the defect, or misses part of it
- **wrong**: it outlines something else (a whole fixture, a different object, blank wall)
- **none**: no red outline (only the blue "pointed here" dot if the inspector pointed, otherwise nothing)

| Run | Correct | Partial | Wrong | None |
|---|---|---|---|---|
| v1 (the first version, the baseline) | 4 | 2 | **6** | 2 |
| **v2 with pointing** (production) | **6** | 2 | **0** | 6 |
| v2 talking-only (pointing ignored, for evaluation only) | 5 | 3 | **0** | 6 |

- v1's six wrong outlines: the whole cooktop (#003), the whole switch plate (#004), the whole mirror
  (#005), a strip across the window (#008), and blobs on blank wall (#009, #010).
- v2's partials: #008 (the box sits mostly on the window header) and #009 (it misses the lower row of
  screws). The talking-only run adds #013 (the whole softener insert).
- v2 only ships when it beats v1 with zero wrong outlines, and it did in both runs. The price is more
  items with no outline (6 of 14). Those keep the "pointed here" dot, or nothing for the voice-only #004.
- 13 of the 14 demo items were pointed at. The talking-only run shows that an inspector who never points
  still gets 5 correct outlines and no wrong ones.
- The contact sheets come from `scripts/contact_sheet.py`, and the re-grounding from
  `punchwalk/pipeline/reground.py`.

## Cost and time per walk

Prices are from `punchwalk/clients/cost.py`:
- SAM 3.1 video: $0.20 per 1,000 frames
- SAM 3.1 images: $2.50 per 1,000
- Muse Voice Transcribe: $0.18 per audio hour
- Muse Spark Open on Baseten: $1.25 / $4.25 per million input / output tokens
- Muse Spark 1.3 fallback: estimated at $3 / $15

Measured on our real glasses footage, from the cost ledger (`data/cost_ledger.jsonl`) for the recorded
runs. Spark Open is priced from the token counts Baseten returned.

| Walk | Length | Output | Live cost | Pipeline time |
|---|---|---|---|---|
| walk1, first walk | 105 s, 5 glasses clips | 12 items | **≈ $0.54**: ≈ $0.22 to hear and track (Muse Voice Transcribe + SAM 3.1 following the hand at 10 fps), ≈ $0.32 to write, outline and check the 12 items | ≈ 4.8 min on a laptop |
| walk2, re-walk | 81 s, 5 clips | 12 status updates checked against open items | **≈ $0.33** | not timed separately |
| walk3, holdout | 16 s, 1 clip | 2 items | **≈ $0.08** | not timed separately |

These are the recorded first-version (v1) runs. Rules of thumb:
- Following the hand costs about $0.20 per 100 s of walk (1,000 frames at 10 fps).
- Each item cost about $0.03 to write, outline and check with v1, and about $0.03–0.05 with grounding v2,
  which adds the locate, frame-pick and check calls.
- **Estimate** for the longest walk we accept (10 minutes, 20 defects): about $1.20 for tracking plus
  $0.60–1.00 for items, so roughly $1.80–2.20. We haven't measured this.

Live latencies from the probe: Files 2.3 s, SAM video 7.0 s for 90 frames, SAM image 1.4–2.9 s, ASR 3.6 s
for 5 s of audio, and Spark Open 4.2 s per call (`fixtures/probe/README.md`).

### Uploading your own walk

Measured in a local Docker dry run at the Fly machine's size (2 CPUs, 2 GB).

- A 26 s walk of two glasses clips took **3.1 min** to process and cost **≈ $0.12**.
- Expect **≈ 3–4 min for a 30 s walk** and **≈ 7–8 min for a 60 s walk** on Fly.
- Each item costs about **$0.03–0.05** with grounding v2 (write, locate, outline, check, verify).
- Best iPhone settings: 1080p at 30 fps, portrait, at most 3 minutes per clip. HDR on is fine (it's
  tone-mapped at ingest), and so is HEVC.
- Say every defect out loud. Items come from what's said, so a silent walk gives 0 items.

**The live app looks after itself.** It re-imports the demo project from a snapshot baked into the image on
every boot and nightly at 03:00 ET, so anything a visitor sends, approves or uploads is undone by morning.
Spend is capped at $10 per day and $60 in total, and uploads at 6 per hour per IP. If today's budget is used
up, an upload is refused with a plain message saying when it resets (midnight ET).

The whole build cost **≈ $3.69** of live Meta and Baseten calls in the development ledger, including probes,
prompt tuning, fixture recording and the grounding v2 runs, against a $25 cap. Replaying a walk from the cache costs $0. That's why the seeded
demo's "How this walk was processed" screen says "from recorded model answers (no new cost)".

## Run it

Requirements: Python 3.11 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm, and ffmpeg. The PDF
report also needs WeasyPrint's Pango libraries, which the Docker image includes.

```bash
make setup          # uv sync + pnpm install
cp .env.example .env  # then fill in MODEL_API_KEY and BASETEN_API_KEY (optional: SLACK_WEBHOOK_URL, RESEND_API_KEY + EMAIL_FROM)
make dev            # API + job worker on :8000 (serves the built PWA)
pnpm -C web dev     # the PWA with hot reload on :5173 (VITE_DATA=live to use the API)
make test           # offline: readonly cache + fixtures, no network
make eval           # eval/report.md from the cache
make seed           # the demo project from recorded walks, at $0 (see below)
make screenshots    # every DESIGN.md screen on the iPhone 15 Pro profile -> .verify/screens/
make docker         # one image: node build -> python 3.11 + ffmpeg + WeasyPrint
make deploy         # fly deploy (first-time setup: docs/DEPLOY.md)
```

**Grounding v2** is on in production (`GROUNDING_V2=1` in `fly.toml`). Locally it's off by default, so
`make dev`, `make seed` and `make eval` replay the recorded v1 answers at $0; set `GROUNDING_V2=1` in
`.env` to run v2 (that makes live calls). The demo project was seeded this way and then re-grounded with
v2 (`punchwalk/pipeline/reground.py`, evidence and outlines only), and the deployed snapshot is exported
from that.

**Demo data.** `make seed` creates the "Unit 2B final punch" project. It has a fictional trades
directory with `@example.com` contacts; Ace Electric has a Slack channel on an example domain that is
never contacted. The seed replays the recorded walks through the real pipeline from the cache, in
readonly mode, at $0. `--walks synth_walk` uses the synthetic walk in `fixtures/pipeline/`. The Linux
image's ffmpeg extracts frames with different bytes than the recording Mac, so on Fly the seed isn't
re-run. Export a snapshot where the walks were recorded (`scripts/seed.py --export`) and import it in
the container (`--import`). The commands are in [docs/DEPLOY.md](docs/DEPLOY.md).

**Layout.**
- `punchwalk/clients/`: the only code that calls the Meta Model API or Baseten, with the cache and cost ledger
- `punchwalk/pipeline/`: the stages
- `punchwalk/api/`: FastAPI and the job worker
- `punchwalk/dispatch/`: Slack, email, Copy link
- `punchwalk/report/`: the PDF
- `web/`: the React PWA
- `eval/`: labels and scoring
- `fixtures/`: recorded responses for offline tests

## Limitations

- **Pointing is optional, but when you do it, the hand has to be in view for about two seconds**, close
  to the defect without covering it. A hand that never holds still, or points off-frame, counts as not
  pointing: the item is still written from what was said, just without the pointed-here dot.
- **Described-only items depend on the camera.** Their photo is picked from the moment the defect was
  described, so look at it while you say it. A subtle defect is easier to outline when you point.
- **Outlines are cautious.** 6 of the 14 demo items have no red outline because no candidate passed the
  score and the check, and two outlines are only partial (#008, #009). A wrong outline is worse than none.
- **One concept per SAM request.** The hand is tracked as "hand", one video request per clip. Each defect
  outline takes one multi-object image request (box and point prompts) plus one concept request, so a
  walk with many defects makes many small SAM calls.
- **No word timestamps.** Muse Voice Transcribe returns turn-level times, so a sentence covering two
  defects is split by meaning, not by the exact second each word was said.
- **Uploads are limited to 10 minutes and 400 MB per walk** (the transcription limit). Longer walks go up as
  separate walks.
- **The hackathon Baseten slug** (`-1026`) returned 404 during the build; the app falls back to the catalog
  slug of the same model and logs which one answered.
- **Replays are byte-exact.** Cached answers are keyed on the exact media, prompts and frames. A different
  ffmpeg build re-extracts frames differently, which is why the deployed demo imports a snapshot.
- One process, two walks at a time, SQLite on a single volume: right for a pilot, not yet for a fleet.

## Roadmap

- **Live, hands-free capture through Meta's Wearables Device Access Toolkit.** The glasses stream straight
  in, items are confirmed by voice as you walk, and a trade looks at a defect and hears its item.
- **Procore / Fieldwire sync**: push items, photos and status both ways.
- **Spec citations**: Muse Spark 1.3's 1M-token context reads the project specs and cites the section each
  item violates.
- **Live captions** on the walk video as it plays.

## Built during the hackathon

**Built during the Meta Global AI Developer Hackathon, Nov 3–4, 2026. Everything in this repo was created during the event.**

All footage and assets are our own: the walks were recorded by the team, and the fixtures and mock media
are synthetic. The build was planned and coordinated by an orchestrator agent (Claude Fable 5.1), and the
code was written by worker agents (Claude Opus 5.5), with every subphase independently verified before it
was committed. The dated list below comes from the git history.

**Nov 3, 2026**
- Scaffold, model clients for Files, Muse Voice Transcribe, SAM 3.1, and Muse Spark Open with its logged fallback. Includes a content-addressed cache and a cost ledger.
- `make probe`: one live call per API to confirm request and response shapes ($0.05). SAM mask coordinate check (masks are box-local).
- Contracts: database models, API schemas, a TypeScript mirror and mock API fixtures, all enforced by a contract test.
- Ingest: ordered clips → portrait-safe display video, 10 fps SAM video, 16 kHz audio, with HDR → SDR.
- Perception pipeline: transcription, turn classification, SAM 3.1 hand tracking for optional pointing (pointing events from the fingertip), fusion, evidence frames.
- Items: Spark Open writes each one, SAM 3.1 outlines the defect (first version, guided by the fingertip), Spark Open verifies it, and a resumable job runner ties the stages together.
- API: SQLite (WAL), range-capable media for iOS video seeking, upload with limits, progress (poll and SSE), the hand overlay, and guards.
- PWA: design system, punch list, item detail, Play this moment, walk detail with the hand overlay, the record flow with a live checklist, and "How this walk was processed".
- Sending to Slack, email or a copied link (Copy link: a shareable message, and the items count as sent), with an auto-send policy (verified items go out, flagged ones wait), and a PDF punch report.
- Docker image and Fly config, and `make seed` (zero-cost replay, plus a snapshot for the deployed image).
- Our own walks: three walks recorded on Ray-Ban Meta glasses (5 clips, 5 clips, 1 clip), with ground truth labeled by the inspector. `make record` captured every model answer ($0.44).
- Eval: label loading, matching (6 s window + Hungarian assignment), metrics and the report. Tuned on walk1 and walk2 until every target was met; walk3 held out.
- Re-walk loop: each status update is mapped to an open item and the before and after photos are compared. Items are signed off with proof, or flagged "marked fixed, but the defect is still visible". Still-open items are sent again, and the re-walk summary screen shows the result.
- Trades screen with the exact message preview, a public share page for each item, the desktop landing page with a QR code, and loading, empty and error states everywhere.
- MIT license. Deployed to Fly.io at https://punchwalk.fly.dev, with the demo project imported from a snapshot.

**Nov 4, 2026**
- HDR → SDR colour fix for Ray-Ban Meta footage (BT.2408 reference white, hable, BT.1886), and the demo's display media re-rendered.
- Grounding v2: Spark Open locates the defect, one multi-object SAM 3.1 request, scored candidates, a zoomed Spark Open check, smoothing. Rated blind against v1 (6 correct and 0 wrong, against 4 and 6); the demo project re-grounded with it.
- Evidence frame picker for items nobody pointed at, or whose frame missed the defect.
- Trades: one vocabulary (To review, To send, Sent), the effective channel named everywhere, history that says what happened.
- The live app re-imports the demo nightly and on boot, with daily and total spend caps and an upload rate limit.
- Live-upload rehearsal, final verification pass, redeploy, demo video.

## License

MIT, see [LICENSE](LICENSE). Copyright 2026 Luciano Bogomolni.
