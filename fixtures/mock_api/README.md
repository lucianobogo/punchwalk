# Mock API

Realistic responses for every GET route in `punchwalk/api/schemas.py`, plus example POST responses and
request bodies. The web app runs on these alone (`VITE_DATA=mock`), and `tests/test_contracts.py`
round-trips every file through its schema.

**Don't edit by hand.** Regenerate with `uv run python scripts/gen_mock_api.py` (about 25 s;
`--no-video` keeps the existing videos). Output is deterministic, so running it twice leaves no diff.
Phase 3 (`scripts/build_mock_api.py`) replaces the synthetic media with real pipeline outputs under the
same file names.

## Naming convention

A request maps to a file like this:

1. Drop the `/api/` prefix and add `.json`: `GET /api/walks/wlk_2b_first/overlay` → `walks/wlk_2b_first/overlay.json`.
   `GET /healthz` → `healthz.json`.
2. A non-GET response adds the method: `POST /api/projects/prj_unit3a/walks` → `projects/prj_unit3a/walks.post.json`.
3. Query strings are ignored. The item list is stored unfiltered; filter and recount it client-side.
4. `GET /api/share/{token}` → `share/{token}.json`.
5. Media URLs in the JSON are root-relative (`/media/...`) and map to `media/...` here.
6. `requests/` holds example request bodies (`<route>.<method>[.<variant>].json`). They aren't responses.

| Route | File | Schema |
|---|---|---|
| `GET /healthz` | `healthz.json` | `HealthOut` |
| `GET /api/projects` | `projects.json` | `ProjectListOut` |
| `GET /api/projects/{id}` | `projects/{id}.json` | `ProjectOut` |
| `GET /api/projects/{id}/items` | `projects/{id}/items.json` | `ItemListOut` |
| `GET /api/projects/{id}/walks` | `projects/{id}/walks.json` | `WalkListOut` |
| `POST /api/projects/{id}/walks` (upload) | `projects/prj_unit3a/walks.post.json` | `UploadOut` |
| `GET /api/projects/{id}/trades` | `projects/{id}/trades.json` | `TradeListOut` |
| `GET /api/projects/{id}/trades/{trade}/preview` | `projects/{id}/trades/{trade}/preview.json` | `DispatchPreviewOut` |
| `GET /api/projects/{id}/trades/{trade}/dispatches` | `projects/{id}/trades/{trade}/dispatches.json` | `DispatchListOut` |
| `POST /api/projects/{id}/trades/{trade}/dispatch` | `projects/{id}/trades/{trade}/dispatch.post.json` (trades with something to send) | `DispatchOut` |
| `GET /api/walks` | `walks.json` (all projects, newest first) | `WalkListOut` |
| `GET /api/walks/{id}` | `walks/{id}.json` | `WalkDetailOut` |
| `GET /api/walks/{id}/progress` | `walks/{id}/progress.json` | `JobProgressOut` |
| (replay of a run) | `walks/wlk_3a_first/progress/01.json` … `08.json` | `JobProgressOut` |
| `GET /api/walks/{id}/overlay` | `walks/{id}/overlay.json` (ready walks) | `OverlayOut` |
| `GET /api/walks/{id}/model-calls` | `walks/{id}/model-calls.json` | `ModelCallListOut` |
| `GET /api/walks/{id}/processing` | `walks/{id}/processing.json` | `ProcessingSummaryOut` |
| `GET /api/walks/{id}/rewalk-summary` | `walks/wlk_2b_rewalk/rewalk-summary.json` | `RewalkSummaryOut` |
| `GET /api/items/{id}` | `items/{id}.json` | `ItemOut` |
| `POST /api/items/{id}/share` | `items/{id}/share.post.json` | `ShareLinkOut` |
| `GET /api/share/{token}` | `share/{token}.json` | `ShareItemOut` |
| `GET /media/...` | `media/...` | files |

There are no mocks for `PATCH /api/items/{id}` or `DELETE /api/items/{id}`. The mock layer applies
patches in memory and returns the patched `ItemOut`. For `GET /api/walks/{id}/events` (SSE), step through
the `progress/NN.json` replay.

## The scenario (ids the UI and API quote)

**Project `prj_unit2b`, "Unit 2B final punch"** (418 Alder Street, Unit 2B). Rooms in walk order: Kitchen,
Bathroom, Bedroom, Hallway, Living room. The trades directory uses fictional `@example.com` contacts. Ace
Electric has a Slack webhook (`#punch-electrical`). Northfield Builders and Clearview Glass have no email.

- **`wlk_2b_first`**: first walk, ready. Ray-Ban Meta glasses, portrait 1080×1920, 153.5 s in 2 clips
  (`walk1_a.mp4` 82.4 s + `walk1_b.mp4` 71.1 s at offset 82.4). 20 transcript lines, 11 tape marks
  (`mrk_2b1_01`–`11`), job `job_2b_first`. It took 1 min 52 s and cost about $0.43, including one
  `meta-fallback` verify call.
- **`wlk_2b_rewalk`**: re-walk of `wlk_2b_first`, ready. 104 s, 17 lines (10 status updates), 4 tape
  marks still on the wall (`mrk_2b2_01`–`04`). Result: "6 signed off. 3 still open. 1 needs a look."

| # | id | Title | Room | Trade | Severity | Status now | Notes |
|---|---|---|---|---|---|---|---|
| 1 | `itm_2b_01` | Missing cabinet knob | Kitchen | carpentry_millwork | cosmetic | closed | before/after |
| 2 | `itm_2b_02` | Coffee drips on counter | Kitchen | cleaning | cosmetic | needs_review | "Marked fixed on the re-walk, but the defect is still visible." |
| 3 | `itm_2b_03` | Bulb out above the sink | Kitchen | electrical | functional | closed | grounding `sam_box` |
| 4 | `itm_2b_04` | Loose towel bar | Bathroom | general | functional | closed | human trade edit (carpentry → general), sent twice |
| 5 | `itm_2b_05` | Toothpaste splatter on mirror | Bathroom | cleaning | cosmetic | closed | |
| 6 | `itm_2b_06` | Door doesn't latch | Bedroom | doors_hardware | functional | closed | |
| 7 | `itm_2b_07` | Scuff marks on wall | Bedroom | painting | cosmetic | dispatched | still open, sent again Nov 4 |
| 8 | `itm_2b_08` | Switch plate crooked | Hallway | electrical | cosmetic | dispatched | still open, sent again Nov 4 |
| 9 | `itm_2b_09` | Window screen missing | Living room | windows_glazing | functional | open | never sent (no contact channel); verified by `meta-fallback` |
| 10 | `itm_2b_10` | Sawdust on floor | Living room | cleaning | cosmetic | closed | grounding `tape_only` |
| 11 | `itm_2b_11` | Outlet cover cracked | Bedroom | electrical | **safety** | dispatched | voice-only (no tape, no mark), `tape_only`; not checked on re-walk |
| 12 | `itm_2b_12` | Tape with no description | Bedroom | general | cosmetic | needs_review | unnarrated tape: "Tape mark with no description" |

Punch list segments: To do 6, Signed off 6, All 12. Needs a look: #2 and #12.

**Project `prj_unit3a`, "Unit 3A turnover"**: a second project for the project switcher.

- **`wlk_3a_first`**: processing. Phone, landscape 1920×1080, 78 s, no tape (all voice-only). Its
  snapshot (`progress.json`, job `job_3a_first`) is mid-run: "Writing items, 2 of 4 written". Items
  `itm_3a_01` and `itm_3a_02` are listed in `projects/prj_unit3a/items.json`.
  `itm_3a_03` and `itm_3a_04` exist only as item files, showing the state after the run finishes;
  the replay's later snapshots reference them. The upload mock (`walks.post.json`) returns this walk,
  so a mock upload can play the replay.

Dispatches: `dsp_2b_01`…`dsp_2b_08` (auto-sends after the first walk, the PM's re-send of #4, and
re-sends of #7 and #8 after the re-walk). Share tokens are `shr_` plus 10 hex characters; every Unit 2B
and 3A item has one.

## Media

- `media/walks/{walk_id}/display.mp4`: H.264 yuv420p + AAC, `+faststart`, 15 fps. **Mock videos are
  downscaled** (720×1280 for portrait walks, 1280×720 for landscape). Their aspect matches the walk, so
  overlay coordinates still line up. The tape in each video moves exactly along `overlay.json`.
- `media/walks/{walk_id}/poster.jpg`: the walk thumbnail.
- `media/items/{item_id}/frame.jpg`: the evidence frame without outlines (`raw_url`). `evidence.jpg` is
  the same frame with the tape (blue) and defect (red, 30% fill) outlined (`image_url`). Both are full
  frames at the walk's pixel size, which is also the space of `tape_svg` / `defect_svg`.
- `media/items/{item_id}/crop.jpg`: 800×600 crop with outlines, used for rows and as `before_image_url`.
- `media/items/{item_id}/after.jpg` (800×600, same framing) and `after_frame.jpg` (full frame): the
  re-walk view for checked items.

Use the overlay's `width`/`height` as the SVG `viewBox` over the video, not the video's pixel size.

The eval block in `walks/*/processing.json` has `source: "mock"`. Those numbers are placeholders until
`eval/report.md` exists. `wlk_3a_first` has `eval: null`, which is the "hide the section" state.
