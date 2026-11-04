# Demo script (3:00)

Recorded on an iPhone with iOS Screen Recording, using the app **installed to the Home Screen** (no Safari
bar). It follows PLAN.md's shot list. Everything runs on the live app, https://punchwalk.fly.dev, with the
seeded demo project "Unit 2B final punch" (`prj_demo`). It has three walks, all our own Ray-Ban Meta
footage:

- `wlk_demo_walk1`: "First walk", 105 s, 5 clips, 12 items
- `wlk_demo_walk2`: "Re-walk" of the same route, 81 s, 12 status updates
- `wlk_demo_walk3`: a first walk in the laundry room, 16 s, 2 items

## The items

| # | Item | Trade | Room | After the re-walk | Used in beat |
|---|---|---|---|---|---|
| #001 | Coffee stains on counter right side | Cleaning | Kitchen | Signed off | |
| #002 | Range hood light bulb missing | Electrical | Kitchen | Signed off | Punch list filter |
| #003 | Stove top damaged at front left burner | Appliances | Kitchen | **Needs a look**: "Marked fixed on the re-walk, but the defect is still visible." | **Re-walk summary (headline moment)** |
| #004 | Light switch cover missing screw | Electrical | Kitchen | Needs a look: "Marked fixed on the re-walk, but the photo doesn't clearly show it." | Punch list filter |
| #005 | Toothpaste smudged on bathroom mirror | Cleaning | Bathroom | Signed off | |
| #006 | Cabinet knob missing on right door | Carpentry & millwork | Bathroom | Signed off | Hook, walk detail (at 0:43) |
| #007 | Fire alarm missing on ceiling | Electrical | Living room | Signed off | Punch list filter |
| #008 | Window screen not attached properly | Windows & glazing | Living room | Signed off | |
| #009 | Wall pencil marks and screws above TV | Painting | Living room | Still open, sent again | Trades (Slack send) |
| #010 | Small wall nick needing repaint, bedroom | Painting | Bedroom | Still open | Trades (Slack send) |
| #011 | Outlet cover loose and crooked in hallway | Electrical | Hallway | Signed off | Item detail, Play this moment |
| #012 | Pencil marks on hallway wall | Painting | Hallway | **Needs a look**: the second false "fixed" claim | Re-walk summary |
| #013 | Mold in washing machine detergent compartment | Cleaning | Laundry room | Needs a look: "The frame doesn't show the defect described." (walk3) | Needs-a-look example |
| #014 | Spilled detergent on laundry machine front | Cleaning | Laundry room | Open (walk3) | |

What the screens show in the seeded state:
- **Punch list:** To do 7 · Signed off 7 · All 14.
- **Re-walk headline:** "7 signed off. 2 still open. 3 need a look."
- **Trades:** Ace Electric has nothing open, because its four items went out automatically after the first
  walk. Three of them were then signed off, and #004 is waiting for a look. Brightline Painting has 2 open
  (#009, #010), so that's the trade to send to on camera (beat 4).

## Pre-flight checklist

- [ ] **Live app up:** `curl -s https://punchwalk.fly.dev/healthz` → `"ok":true`. On the phone, the home screen shows "Unit 2B final punch".
- [ ] **Demo project imported**, with all three walks ready:
  - `/walks` lists all three walks as Ready.
  - The punch list reads "To do 7 · Signed off 7 · All 14".
  - `/walks/wlk_demo_walk2/rewalk` shows "7 signed off. 2 still open. 3 need a look.", with the #003 stovetop card first.
  - If not, run the snapshot `--import` command in [DEPLOY.md](DEPLOY.md) ("On Fly, import a snapshot").
- [ ] **Reset between full rehearsals.** Sending, approving and uploading all change the demo data. The same `--import` command puts the demo project back exactly as seeded. It replaces the project, so redo the Slack swap below afterwards.
- [ ] **Slack for beat 4 (optional, recommended).** The seeded Ace Electric webhook is on an example domain and is never contacted. For a message that really lands, put a real incoming webhook on **Brightline Painting**. Its two open items stay open after a send, so you can retake the shot as often as you like. This command never prints the URL:
  ```bash
  APP=https://punchwalk.fly.dev; TRADE=painting; CHANNEL='#punch-painting'   # CHANNEL: the channel the webhook posts to (shown in the app)
  read -rs HOOK   # paste the Slack incoming-webhook URL, press Enter
  curl -s $APP/api/projects/prj_demo | HOOK="$HOOK" TRADE="$TRADE" CHANNEL="$CHANNEL" python3 -c "
  import json,sys,os; p=json.load(sys.stdin); e=os.environ
  t=[{k:v for k,v in c.items() if k!='has_slack_webhook'} | ({'slack_webhook':e['HOOK'],'slack_channel':e['CHANNEL']} if c['trade']==e['TRADE'] else {}) for c in p['trades']]
  print(json.dumps({'trades':t}))" | curl -s -X PATCH -H 'content-type: application/json' -d @- $APP/api/projects/prj_demo \
    | python3 -c "import json,sys; print([(t['company'], t['slack_channel'], t['has_slack_webhook']) for t in json.load(sys.stdin)['trades']])"
  ```
  Expect `('Brightline Painting', '#punch-painting', True)` in the printed list. Other trades keep their settings, because a trade sent without a webhook keeps its stored one. If you skip this, beat 4 uses the Outbox instead ("Copy 2 items for Brightline Painting"). That's still a real send, but there's no Slack message to cut to.
- [ ] **A fresh walk to upload for beat 2:** 2–3 short glasses clips in Photos (e.g. 30 s with 2 defects). The upload makes live calls. **Estimate:** about $0.10 and 1–2 minutes (measured for comparison: walk3, 16 s with 2 items, cost about $0.08; walk1, 105 s, took about 4.8 min). Start it before you film the other beats, then film the checklist while it fills in. It adds items #015 onward to the demo project and auto-sends the verified ones (to Slack, if their trade has the real webhook). So film beats 3–6 first, or reset afterwards.
- [ ] Phone: Do Not Disturb on, battery 100% (no low-power banner), brightness high, portrait lock on, Wi-Fi strong. Close other apps.
- [ ] App opened from the Home Screen icon, and scrolled to the top on each screen before recording.
- [ ] Desktop browser open at `https://punchwalk.fly.dev/landing` for the last beat (QR code + phone frame).

## Beats

### 0:00–0:12 Hook

- **Shoot:** 2 s of the inspector in Ray-Ban Metas pointing at the bathroom cabinet's missing knob (third person, our own footage). Then cut to the walk detail (`/walks/wlk_demo_walk1`), scrubbed to about 0:42 and playing: the hand is outlined in blue, tab 6 sits at the fingertip, and the "Cabinet knob missing on right door" card slides up with "New".
- **Must be visible:** the hand outline, the number tab at the fingertip, the new-item card.
- **Say:** "The walk takes minutes. The write-up takes hours, and the final payment waits on it."

### 0:12–0:40 Record

- **Taps:** Record tab (`/record`) → project "Unit 2B final punch" → "First walk" → "Add clips" (pick the glasses clips; drag to reorder if needed) → **Upload walk** → the checklist fills in.
- **Must be visible:** the step lines that name the models:
  - "Listening to the walk": "Muse Voice Transcribe found N lines"
  - "Following your hand (SAM 3.1)": "N points found"
  - "Matching what was said to each point": "N matched to where you pointed"
  - "Writing items": "Muse Spark Open on Baseten, N of M written"
  - "Outlining each defect" (SAM 3.1) and "Checking each item against its photo" (Muse Spark Open on Baseten)
  - the "Items so far" thumbnails with their number tabs
- **Say:** "I point, I say what's wrong. Muse Voice Transcribe hears it, SAM 3.1 follows my hand to the spot, and Muse Spark Open on Baseten writes each item."
- **Tip:** cut away while it runs. Come back for the last two steps and "N items ready".
- **Backup:** if the upload can't run, film the clip picker up to **Upload walk** and lay this voiceover over the hook footage. Don't use the mock-data checklist screenshot in the video.

### 0:40–1:30 Punch list, Play this moment, walk detail

- **Taps:**
  1. Punch list tab (`/`): "To do 7", with "Needs a look 4" pinned first (#003, #004, #012, #013).
  2. Tap **All 14**, then the Trade chip → **Electrical**. Four rows: #002 bulb, #004 switch screw (Needs a look), #007 fire alarm, #011 outlet cover. Optional: Severity → **Functional** leaves #002 and #011.
  3. Tap **#011 Outlet cover loose and crooked in hallway** → item detail. It was signed off on the re-walk, so the photo is a before/after slider: drag it once. Show the action, and the quote card with the inspector's own words: "The hallway the outlet cover is not tightened and it's twisted."
  4. Tap **Play this moment** on the quote card. The sheet starts at 1:26, two seconds before the quote. The caption shows the quote, and playback pauses at the end.
  5. Back to Walks → "First walk" (`/walks/wlk_demo_walk1`). Play from about 0:35: #005 and then #006 slide in over the video as they're called out, and the timeline has 12 markers. Scroll so the video collapses to the mini-player.
- **Must be visible:** the number tabs on rows, the AI summary and the action, the inspector's quote as the source, the timeline markers.
- **Say:** "Every item carries its proof: the photo with the defect outlined, the exact second of video, and my own words. Nobody has to watch the whole walk."

### 1:30–2:00 Trades → Slack, and Needs a look

- **Taps:** Trades tab (`/trades`). Hold 1 s on the list: Ace Electric reads "Electrical. Nothing open, 1 needs a look." and "Slack, last sent Nov 3", because its items went out automatically after the first walk. Tap **Brightline Painting** (`/trades/painting`).
  - With the Slack swap done, Slack is preselected. The preview shows the message exactly as it will be sent: "Unit 2B final punch: 2 painting items", then #009 and #010, each with its location, the action, and a "View item and video" link.
  - Tap **Send 2 items to Brightline Painting**. The toast reads "Sent 2 items to Brightline Painting in Slack". Cut to Slack on a second phone or laptop: the message landed.
  - Without the swap, the button reads "Copy 2 items for Brightline Painting", and the toast says it's copied and in the Outbox.
- **Then:** Punch list → **#013 Mold in washing machine detergent compartment** (Needs a look). Show its note, "The frame doesn't show the defect described.", and the **Approve and send to Spotless Final Clean** button. Don't tap it during takes; it sends the item and changes the data.
- **Must be visible:** "Message to #punch-painting" (or "Message to copy and share" without Slack), the item lines, the Slack message itself, the #013 note.
- **Say:** "Verified items go straight to the trade. When the photo doesn't back up the description, it waits for a human."
- **Ace Electric instead (one take only):** run the swap with `TRADE=electrical; CHANNEL='#punch-electrical'`, then open #004 and tap **Approve and send to Ace Electric**. It posts one item to Slack, and after that #004 is no longer waiting. Reset with the import before a retake.

### 2:00–2:35 Re-walk summary

- **Taps:** Walks → "Re-walk" → summary (`/walks/wlk_demo_walk2/rewalk`).
- **Must be visible:**
  - the headline "7 signed off. 2 still open. 3 need a look." with its bar
  - the flagged card first: **#003 Stove top damaged at front left burner**, "Marked fixed on the re-walk, but the defect is still visible.", the inspector's claim ("On the re-walk: 'The damaged stove top is fixed'"), and the before and after photos (Nov 3 / Nov 3, re-walk) with the blue dot on both
  - scroll past #004 and #012 (the second false claim) to the signed-off list and the 2 still open (#009, #010)
- **Say:** "On the re-walk I said the stovetop was fixed. It wasn't, and Punchwalk caught it."
- **Tip:** hold on the #003 card for 2 s. This is the headline moment.

### 2:35–3:00 How it was processed, eval, built Nov 3–4, live URL

- **Taps:** Walks → "First walk" → "How this walk was processed" (`/walks/wlk_demo_walk1/processing`). Show the step list with its model chips (Muse Voice Transcribe, SAM 3.1, Muse Spark Open on Baseten), then scroll to the eval block "On our labeled test walks":
  - Defects found: 12 of 12
  - Right trade: 12 of 12
  - Right room: 12 of 12
  - False "fixed" claims caught: 2 of 2

  Then cut to the desktop landing page (`/landing`): the live app in the phone frame and the QR code.
- **Note:** the subtitle on this screen reads "Done in 1 min 34 s from recorded model answers (no new cost), using three Meta models.", because the demo replays the recorded run. Say the live numbers instead, or put them on a caption: the live run of this 105 s walk took about 4.8 min and cost about $0.54.
- **Must be visible:** the three model names, the eval numbers, the URL and the QR code.
- **Say:** "Three Meta models, and every reasoning step runs on open-weights Muse Spark Open. This 105-second walk took under five minutes and about 54 cents. Built during the hackathon, Nov 3 and 4. Try it at punchwalk.fly.dev."

## iOS Screen Recording tips

- Add Screen Recording to Control Center (Settings → Control Center). Start it from Control Center and wait for the 3-second countdown to finish before the first tap.
- Record each beat as its own clip, and cut them together in Photos or iMovie. Trim the red status pill at the start and end.
- Turn on Do Not Disturb, so no notifications or the Dynamic Island interrupt. Use 100% battery, or stay plugged in so the low-power banner never shows.
- Keep the phone in portrait and open the app from the Home Screen. A Safari tab shows the address bar; the installed app doesn't.
- Tap deliberately and pause about 1 s on each screen. Screen recordings exaggerate fast scrolling.
- Record the voiceover separately and lay it over the cut, so taps don't have to match speech.

## 30–45 s cut for X

1. **0–5 s:** the glasses view pointing at the cabinet's missing knob: the hand outlined, tab 6 at the fingertip. Caption: "Point at it. Say it."
2. **5–15 s:** the processing checklist naming Muse Voice Transcribe, SAM 3.1 and Muse Spark Open on Baseten → "N items ready".
3. **15–25 s:** #011 item detail with the before/after photo, then Play this moment jumping to the second.
4. **25–35 s:** "Send 2 items to Brightline Painting" → the Slack message lands.
5. **35–45 s:** the #003 re-walk card: "Marked fixed on the re-walk, but the defect is still visible." End card: "Punchwalk. Built on Meta's open models during the Meta Global AI Developer Hackathon. punchwalk.fly.dev"
