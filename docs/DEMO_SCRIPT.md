# Demo script (3:00)

Recorded on an iPhone with iOS Screen Recording, using the app **installed to the Home Screen** (no Safari
bar). It follows PLAN.md's shot list. The demo project is "Unit 2B final punch" (`prj_demo`, created by
`make seed` or a snapshot import). Its walks are `wlk_demo_walk1` (first walk) and `wlk_demo_walk2`
(re-walk).

Item numbers come from the real walk, so this script names items by what they are: "the cabinet knob
item", "the switch plate item". Fill the numbers in after seeding. The staging (FOOTAGE.md) has a cabinet
knob, coffee drips, a bulb out, a loose towel bar, a mirror splatter, a door that won't latch, wall scuffs,
a crooked switch plate, a missing window screen, floor debris, and one defect described without pointing.

| Item | Number after seeding |
|---|---|
| Cabinet knob (Kitchen, carpentry) | `#__` |
| Bulb out (Kitchen, electrical, functional) | `#__` |
| Switch plate (Hallway, electrical) | `#__` |
| The item claimed "fixed" on the re-walk but not fixed | `#__` |
| An item flagged "Needs a look" after verification | `#__` |

## Pre-flight checklist

- [ ] **Live app up:** `curl -s https://punchwalk.fly.dev/healthz` → `"ok":true`. The home screen shows "Unit 2B final punch".
- [ ] **Demo project present** with both walks ready:
  - `/walks` lists "First walk" and "Re-walk", both Ready
  - `/walks/wlk_demo_walk2/rewalk` shows the headline ("N signed off. N still open. 1 needs a look.")
- [ ] **Re-walk processed**, and the false-fix item reads "Marked fixed on the re-walk, but the defect is still visible."
- [ ] **Slack for beat 4.** The seeded Ace Electric webhook is on an example domain and is never contacted, so for a message that really lands, give Ace Electric a real webhook. This command never prints the URL:
  ```bash
  APP=https://punchwalk.fly.dev; read -rs HOOK   # paste the Slack incoming-webhook URL, press Enter
  curl -s $APP/api/projects/prj_demo | HOOK="$HOOK" python3 -c "
  import json,sys,os; p=json.load(sys.stdin); hook=os.environ['HOOK']
  t=[{k:v for k,v in c.items() if k!='has_slack_webhook'} | ({'slack_webhook':hook} if c['trade']=='electrical' else {}) for c in p['trades']]
  print(json.dumps({'trades':t}))" | curl -s -X PATCH -H 'content-type: application/json' -d @- $APP/api/projects/prj_demo \
    | python3 -c "import json,sys; print([(t['company'], t['has_slack_webhook']) for t in json.load(sys.stdin)['trades']])"
  ```
  Expect `('Ace Electric', True)` in the printed list. Other trades keep their settings. If you skip this, the send still works, but it shows "Sent to Ace Electric in Slack (#punch-electrical)" with nothing posted. In that case, film the trade preview and the toast, and use the Outbox ("Copy link").
- [ ] **A fresh walk to upload for beat 2:** 2–3 glasses clips in Photos (e.g. a short walk of 2 defects). Uploading makes live calls (a few cents) and takes 2–4 minutes. Start it before you film, then film the checklist while it fills in.
- [ ] Phone: Do Not Disturb on, battery 100% (no low-power banner), brightness high, portrait lock on, Wi-Fi strong. Close other apps.
- [ ] App opened from the Home Screen icon, and scrolled to the top on each screen before recording.
- [ ] Desktop browser open at `https://punchwalk.fly.dev/landing` for the last beat (QR code + phone frame).

## Beats

### 0:00–0:12 Hook

- **Shoot:** 2 s of the inspector in Ray-Ban Metas pointing at a defect (third-person, our own footage). Then the walk detail, `/walks/wlk_demo_walk1`, playing that moment: the hand outlined in blue, the number tab at the fingertip, and the new-item card sliding up.
- **Must be visible:** the hand outline, the number tab at the fingertip, the new-item card.
- **Say:** "The walk takes minutes. The write-up takes hours, and the final payment waits on it."

### 0:12–0:40 Record

- **Taps:** Record tab (`/record`) → project "Unit 2B final punch" → "First walk" → "Add clips" (pick the glasses clips; drag to reorder if needed) → **Upload walk** → the checklist fills in.
- **Must be visible:** the step lines that name the models:
  - "Listening to the walk: Muse Voice Transcribe found N lines"
  - "Following your hand (SAM 3.1): N points found"
  - "Writing items: Muse Spark Open on Baseten, N of M written"
  - the "Items so far" thumbnails with their number tabs
- **Say:** "I point, I say what's wrong. Muse Voice Transcribe hears it, SAM 3.1 follows my hand to the spot, and Muse Spark Open on Baseten writes each item."
- **Tip:** cut away while it runs. Come back for the last two steps and "N items ready".

### 0:40–1:30 Punch list, Play this moment, walk detail

- **Taps:**
  1. Punch list tab (`/`): "To do", with "Needs a look" pinned first.
  2. Trade chip → **Electrical**, Severity chip → **Functional**. Leaves the bulb item.
  3. Tap the bulb item row → item detail (`/items/<id>`). Show the big photo: the red outline and the blue "pointed here" dot. Toggle "Outlines on" off and on.
  4. Tap **Play this moment** on the quote card. The sheet starts 2 s before, the caption shows the quote, and playback pauses at the end.
  5. Back to Walks → "First walk" (`/walks/wlk_demo_walk1`). Play: items slide in over the video as they're called out. Scroll so the video collapses to the mini-player.
- **Must be visible:** the number tabs on rows, the AI summary and the action ("Replace the bulb"), the inspector's quote as the source, the timeline markers.
- **Say:** "Every item carries its proof: the photo with the defect outlined, the exact second of video, and my own words. Nobody has to watch the whole walk."

### 1:30–2:00 Trades → Slack

- **Taps:** Trades tab (`/trades`) → **Ace Electric** (`/trades/electrical`). Slack is preselected, and the preview shows the message exactly as sent: a bold first line, then each item with its tab, title, location plus action, and a "View item and video" link. Tap **Send N items to Ace Electric**. The toast reads "Sent N items to Ace Electric". Cut to Slack on a second phone or laptop: the message landed, with the evidence thumbnails.
- **Then:** back on the punch list, open the item marked **Needs a look** (the frame doesn't support the description). Show its note and the "Approve and send to <company>" button.
- **Must be visible:** "Message to #punch-electrical", the item lines, the Slack message itself.
- **Say:** "Verified items go straight to the trade. When the photo doesn't back up the description, it waits for a human."

### 2:00–2:35 Re-walk summary

- **Taps:** Walks → "Re-walk" → summary (`/walks/wlk_demo_walk2/rewalk`).
- **Must be visible:**
  - the headline "N signed off. N still open. 1 needs a look." with its bar
  - the flagged card first: "Marked fixed on the re-walk, but the defect is still visible.", the before and after photos with dates, the blue dot on both
  - then the signed-off list
- **Say:** "On the re-walk I said it was fixed. It wasn't, and Punchwalk caught it."
- **Tip:** hold on the flagged card for 2 s. This is the headline moment.

### 2:35–3:00 How it was processed, eval, built Nov 3–4, live URL

- **Taps:** First walk → "How this walk was processed" (`/walks/wlk_demo_walk1/processing`). The line "Done in N min N s for about $N, using three Meta models", the step list with model chips (Muse Voice Transcribe, SAM 3.1, Muse Spark Open on Baseten), and the eval block "On our labeled test walks". Then cut to the desktop landing page (`/landing`): the live app in the phone frame and the QR code.
- **Must be visible:** the real time and cost, the three model names, the eval numbers (only if `eval/report.md` exists), the URL and the QR code.
- **Say:** "Three Meta models, every reasoning step on open-weights Muse Spark Open. Built during the hackathon, Nov 3 and 4. Try it at punchwalk.fly.dev."

## iOS Screen Recording tips

- Add Screen Recording to Control Center (Settings → Control Center). Start it from Control Center and wait for the 3-second countdown to finish before the first tap.
- Record each beat as its own clip, and cut them together in Photos or iMovie. Trim the red status pill at the start and end.
- Turn on Do Not Disturb, so no notifications or the Dynamic Island interrupt. Use 100% battery, or stay plugged in so the low-power banner never shows.
- Keep the phone in portrait and open the app from the Home Screen. A Safari tab shows the address bar; the installed app doesn't.
- Tap deliberately and pause about 1 s on each screen. Screen recordings exaggerate fast scrolling.
- Record the voiceover separately and lay it over the cut, so taps don't have to match speech.

## 30–45 s cut for X

1. **0–5 s:** the glasses POV, pointing at a defect; the hand outlined, the number tab at the fingertip. Caption: "Point at it. Say it."
2. **5–15 s:** the processing checklist naming Muse Voice Transcribe, SAM 3.1 and Muse Spark Open on Baseten → "N items ready".
3. **15–25 s:** an item detail with the outlined defect and the blue dot, then Play this moment jumping to the second.
4. **25–35 s:** "Send N items to Ace Electric" → the Slack message lands.
5. **35–45 s:** the re-walk card "Marked fixed on the re-walk, but the defect is still visible." End card: "Punchwalk. Built on Meta's open models during the Meta Global AI Developer Hackathon. punchwalk.fly.dev"
