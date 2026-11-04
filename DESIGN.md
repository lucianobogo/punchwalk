# DESIGN: Punchwalk

**Phone first.** A project manager opens Punchwalk on an iPhone between site meetings and sees three things without watching any video: what's wrong, who owns it, and the proof. Judges see these same phone screens in the demo video. This file wins any conflict about how the app looks or reads.

**Visual target:** `docs/design/` holds reviewed renders of every key screen (iPhone 15 Pro, 393 × 852, plus the desktop landing page). Open them with Read before building a screen and match them. Where a render and this file disagree, this file wins.

| Render | Screen |
|---|---|
| `01-punch-list.png` | Punch list (home) |
| `02-item-detail.png` | Item detail |
| `03-play-this-moment.png` | Play this moment |
| `04-walk-detail.png` | Walk detail, at the moment an item is created |
| `05-record-processing.png` | Record: processing |
| `06-send-to-trade.png` | Send to a trade |
| `07-rewalk-summary.png` | Re-walk summary |
| `08-how-processed.png` | How this walk was processed |
| `09-landing-desktop.png` | Landing page on desktop |

The renders use drawn stand-ins for photos and video. The real app shows real evidence frames. The renders were made before the pointing change: the blue tape strips in them are placeholders for real frames (the app shows the defect outline plus a blue "pointed here" dot, and a hand outline in video). Everything else in them still holds.

## Direction

- **Subject:** the end-of-job punch walk: primed drywall, an inspector pointing at each problem and saying what's wrong, redlines on drawings, sign-off sheets.
- **Feel:** calm, exact, field-ready. White surfaces like primed drywall, graphite text, and one bold device.
- **The memorable thing is the tape tab:** each item's number written in graphite "marker" on a little strip of light blue painter's tape, the way punch lists used to be marked on the wall. A tab sits on the corner of every evidence photo and thumbnail, follows the fingertip in the video while the inspector points, and leads list rows. Everything around it stays quiet and disciplined.

## Color

Light mode is the default, because job sites are bright.

| Name | Hex | Use |
|---|---|---|
| Primer | `#F2F4F5` | App background |
| Sheet | `#FFFFFF` | Cards, rows, sheets, bars |
| Graphite | `#1B2430` | Primary text, icons, tab numbers |
| Pencil | `#5B6673` | Secondary text |
| Hairline | `#DCE1E5` | Borders, dividers |
| Tape tab | `#5AA2EA` | Tab fill (Graphite number on it: 5.8:1) |
| Tape | `#2F86E0` | The "pointed here" dot in photos, hand outlines in video, the Record button, progress |
| Tape ink | `#1D63B5` | Blue text, links, primary buttons (white text on it passes AA) |
| Tape tint | `#E8F1FB` | Play buttons, the selected-row background |
| Redline | `#D93B30` | Defect outlines + 30% fill (white halo underneath) |
| Safety | `#FBE9E7` bg, `#A3241B` text | The "Safety" severity pill |
| Signed off | `#2F9E6B` fills, `#1F7A50` text | Signed-off badge and label |
| Hold | `#C9861A` fills, `#8A5A0B` text | "Needs a look" dot and label; flagged card `#FFFBF3` with border `#EBD3A6` |

- No gradients and no colored left-border accents.
- The only translucency is the tab bar and the bottom action bar (white at 94%).
- The color names "Tape", "Tape tab", "Tape ink" and "Tape tint" are kept from the painter's-tape palette; they no longer refer to tape in the footage.
- Dark mode *(nice)*: background `#0F151C`, sheets `#18202A`, text `#E8ECEF`, Pencil `#9AA5B1`, Hairline `#2A3440`. The blue and red stay the same.

## Type

**Archivo** (variable, self-hosted via `@fontsource-variable/archivo`) for everything. The width axis does the work: normal width for reading text, and **Archivo Expanded** (`font-stretch: 125%`, or 112% for 22 px titles) for screen titles, tab numbers and the landing headline.

| Role | Size / line height | Style |
|---|---|---|
| Top-level screen title | 28 / 32 | Expanded 125%, semibold |
| Pushed screen title | 22 / 28 | Expanded 112%, semibold |
| Row / card title | 15 / 20 (rows), 17 / 22 (cards) | Semibold |
| Body | 15 / 21 | Regular |
| Secondary | 13 / 18 | Regular, Pencil |
| Section header | 13 / 16 | Medium, Pencil, sentence case |
| Tab number | 15 (40 px tab), 13 (32 px tab) | Expanded 125%, bold, Graphite |
| Tab bar label | 11 / 13 | Medium (semibold when active) |

- Tabular numerals for counts, times and money.
- Sentence case everywhere. No all-caps labels, and no small labels stacked above headings.

## Shape, depth, spacing

- 4 px grid; 16 px screen gutters; 8 px between rows; 12 px between cards.
- Radii by role: sheets 20, cards and rows 14, images 10–14, buttons 12, chips and pills fully rounded.
- Depth: rows and cards get a 1 px Hairline border and no shadow. One soft shadow is reserved for sheets and the new-item card over video.
- Minimum touch target 44 × 44 px. Visual elements can be smaller (a 34 px play circle) inside a 44 px hit area.

## Signature component: the tape tab

The item's number tab. It keeps the look of a strip of painter's tape, but it's a UI element only: there is no physical tape in the footage.

- A strip in Tape tab blue, rotated −3°, with slightly ragged left and right ends. Draw it as an SVG path, not a CSS rectangle. The path used in the renders: `M3 0H37L39 3L37 6L40 9L37 12L39 15L37 18L40 21L37 24H3L1 21L3 18L0 15L3 12L1 9L3 6L0 3Z` on a 40 × 24 viewBox.
- Two sizes: **40 × 24** for photos, video and detail headers; **32 × 20** for list rows, thumbnails and message previews.
- The number is Graphite, Expanded, bold: `7`, `12`. No "#" and no leading zeros.
- **On photos and thumbnails:** it overlaps the top-left corner by 6–8 px, as if stuck on.
- **In the video overlay:** while the inspector points, it sits at the fingertip, next to the blue hand outline.
- **"Pointed here" dot:** on evidence photos and thumbnails, a Tape-blue dot (6–8 px radius at full frame, white halo) marks where the finger pointed. Drawn with the defect outline and hidden by the outline toggle with it.
- **Status badge** on the tab's top-right corner:
  - Hold dot for Needs a look
  - white check on Signed off green for signed off

  Color is never the only signal: the status also appears in words (section header, details, labels).

## Statuses (UI)

Three statuses, everywhere: **Open**, **Needs a look**, **Signed off**.

- Internal `open` and `dispatched` both show as **Open**. Sending is a fact about the item ("Open, sent to Oakline Millwork Nov 3"), shown in Details and History, not a status.
- `needs_review` → **Needs a look**. `closed` → **Signed off**.
- Actions keep their names through the flow: the button "Sign off" leads to the status "Signed off".

## Motion

- **One orchestrated moment.** On walk detail, when the video reaches the second an item was called out:
  - its number tab is "pressed on" at the fingertip (scale 1.06 → 1, 180 ms)
  - a new-item card slides up over the bottom of the video, labeled "New"
  - after ~2 s the card settles into the list below
- Everything else only responds to the user: sheets slide up, chips toggle, the before/after slider follows the finger. Use 150–250 ms, ease-out.
- With `prefers-reduced-motion`, use fades only.
- No entrance animations on page load, and no hover effects on cards.

## Navigation (iPhone 15 Pro, 393 × 852)

- **Bottom tab bar**, four labeled tabs:
  1. Punch list
  2. Walks
  3. **Record**: a filled Tape-blue rounded button (44 × 28) with a white record icon (ring + dot) and the "Record" label below it
  4. Trades
- Top-level screens have a large title that collapses into a compact bar on scroll. Pushed screens have a "‹ Back" link in Tape ink and a 22 px title.
- Item detail pushes in from the right. Filters, search, Play this moment, More actions and send confirmations are bottom sheets (`vaul`).
- **Desktop:** the app renders in a centered 440 px column on Primer. The landing page is the one exception (below).

## Screens

The `docs/design/*.png` renders show blue tape strips in the photos and video. They are placeholders for real frames: build the red defect outline, the blue "pointed here" dot and (in video) the hand outline described below instead.

### Punch list (home), `01-punch-list.png`

- **Title:** the project name with a chevron. Tapping it switches projects.
- **Status segments:** "To do 6", "Signed off 6", "All 12". To do = Open + Needs a look, and it's the default.
- **Filter chips:** Trade, Severity, Room. Each opens a sheet of checkable options with their counts ("Electrical 3") and a button that says "Show 3 items". Active chips fill with Tape tint and get a clear control. A search button at the end of the row opens search plus group and sort (group by room, the default, in walk order, or by trade; sort by walk order or severity).
- **Needs a look first:** a section with a Hold dot and count, then sections by room in walk order.
- **Compact rows** (not big cards; about five fit on screen):
  - an 88 × 66 thumbnail (the 4:3 evidence crop, centered on where the finger pointed + the defect) with its tape tab on the corner
  - the title, one line
  - a summary of up to 2 lines in Pencil (for Needs a look: why it needs a look)
  - a "Safety" pill with a warning icon, only when the severity is Safety
  - a play button on the right (34 px circle, 44 px target) that opens Play this moment
  - tapping the row opens item detail
- Filters are kept in the URL, so a filtered view can be shared.
- **Empty states:**
  - No filter results: "No items match these filters." with a "Clear filters" button.
  - First run: "No walks yet. Point at each problem, say what's wrong, and record the walk." with a "Record a walk" button.
  - To do empty: "Everything is signed off." with a "See signed-off items" button.

### Item detail, `02-item-detail.png`

- The evidence photo sits inset 16 px with radius 14, with the tape tab on its corner, pinch-to-zoom, and an "Outlines on" toggle. With outlines on, the defect is outlined in red and a small blue dot marks where the finger pointed; off shows the plain frame. Voice-only items show the frame with no dot. Signed-off items show a before/after slider instead.
- Then, in order:
  1. The title (22 px) and the AI summary.
  2. The action, on a line with a wrench icon.
  3. The quote card: play circle, the quote, "Play this moment, 0:14".
  4. Details as a key-value list: Room, Trade, Severity, Status ("Open, sent to Oakline Millwork Nov 3").
  5. Checked: "Matches the photo and what was said", or the verifier's note.
  6. History timeline.
- **Bottom bar:** one primary action that depends on the status, plus a "More" button (opens a sheet):
  - **Open:** primary "Sign off". More: Send again, Mark as needs a look, Edit.
  - **Needs a look:** primary "Approve and send to <company>" (after a look at the photo, or an edit when the photo doesn't show the defect). More: Dismiss item, Edit.
  - **Signed off:** primary "Reopen".
- An edit icon in the top bar opens editing. Every edit is a human override and shows up in History.

### Play this moment (sheet), `03-play-this-moment.png`

- A dark sheet (`#0F151C`) with its tape tab, title and a close button. Portrait glasses video fills the width (3:4), with outlines on (the hand outline and number tab while the inspector points).
- Playback starts at `video_start_s` (2 s before the item was called out, so the hand is seen coming in). A caption box shows the quote while it's spoken, and playback pauses at `video_end_s`.
- **Controls:**
  - a scrubber limited to the moment
  - the current time on the left and "Moment ends 0:16" on the right
  - Replay, a large play/pause button, and "Keep watching" (removes the limit)

### Walks

- **List rows:** thumbnail, date and time, duration, items found, and status ("Processing", "Ready", or "Failed. Tap to retry").
- **Walk detail (the demo's hero screen), `04-walk-detail.png`:**
  - Title row: "First walk" with "Nov 3, 12 items" on the right.
  - The portrait video is ~500 px tall. While the inspector points, the hand is outlined in Tape blue and the item's tape tab sits at the fingertip (from the overlay's `hands`); nothing is drawn between points.
  - Compact controls sit over the bottom of the video: play/pause, a timeline with a Tape-tab-blue marker for every item (each marker seeks; 44 px hit area), and the current time.
  - The new-item card appears over the video at its moment (see Motion).
  - Below the video, a segmented control switches between "Items so far N" and "Transcript". Tapping a row or a line seeks.
  - When the list scrolls, the video collapses into a 16:9 mini-player pinned at the top (cover crop). Tap it to expand.
  - At the bottom, a link: "How this walk was processed".

### Record, `05-record-processing.png`

One screen, top to bottom:
1. Choose the project.
2. "First walk" or "Re-walk" (segmented control).
3. "Add clips" from Photos or Files. Clips are reorderable, since glasses walks arrive in parts.
4. "Upload walk", with a progress bar.

Then a checklist card fills in live. Each step has a status icon (check, progress ring, empty ring), the step name, and one plain line that names the model and the count:

- "Listening to the walk": "Muse Voice Transcribe found 23 lines"
- "Following your hand (SAM 3.1)": "11 points found"
- "Matching what was said to each point": "11 matched to where you pointed, 1 described without pointing"
- "Writing items": "Muse Spark Open on Baseten, 7 of 12 written", with a progress bar
- "Checking each item against its photo": "Muse Spark Open on Baseten, waiting"

Below the checklist, an "Items so far" strip of thumbnails with their tabs, and the line "You can leave this screen. Items appear on the punch list as they're written." When it finishes: "12 items ready" and an "Open punch list" button.

### Trades

The render `06-send-to-trade.png` predates this section; where they differ, this section wins.

- **One vocabulary per trade** (signed-off items aren't counted anywhere on these screens):
  - **To review**: items that need a look before they can go out. Amber (Hold).
  - **To send**: open items never sent to this trade. Blue (Tape tint chip, Tape ink text).
  - **Sent**: open items already sent on any channel, including a copied link. Muted (Pencil).
- **The effective channel** is the one a send will use, and the app names it everywhere (list row, the default in the picker, the button, history, the Approve-and-send confirmation): the trade's own channel if it's set up and working, else the team's Slack channel if the team webhook is set, else Copy link. Only working channels are offered; a channel that can't be used isn't shown, not even grayed out.
- **List:**
  - each row: the company (or the trade name and "No company yet"), the trade in plain words, chips "1 to review" (amber) and "2 to send" (blue), a muted "3 sent", and one line naming the channel: "Posts to #punch-electrical." / "Posts to your team's Slack channel." / "Emails maria.chen@example.com." / "Copy a link to share." (the API's `effective_channel_label`)
  - no count badge: the chips are the counts
  - trades with nothing to review or send go under an "All caught up" section header, below the rest
- **Trade screen:**
  - the company name, then the counts as a sentence: "1 to review, 2 to send, 3 sent." (or "All caught up.")
  - if anything is to review: an amber row "1 item needs your review before it can go out" that opens the item (with more than one, it lists them, each opening its item)
  - a channel segmented control only when more than one working channel exists; otherwise the channel line alone ("Posts to #punch-electrical.")
  - the message preview exactly as it will be sent: a bold first line, then each To send item with its tape tab, title, one line of location plus action, and a "View item and video" link; each item checked by default
  - "Already sent": the items already sent, each with when and where ("Sent Nov 3 at 7:41 PM, Slack"), unchecked; checking one sends it again
  - the primary button names the outcome: "Post 2 items to Slack" / "Email 2 items to Brightline Painting" / "Copy a link for 2 items"
  - history: what happened, with date and time ("Posted 2 items to #punch-electrical", "Emailed 2 items to Brightline Painting", "Copied a link for 3 items"; the API's `summary`); "Nothing sent to Ace Electric yet." when empty
- After sending, a toast with the same sentence ("Posted 2 items to #punch-electrical").
- **Approve and send** (item detail) confirms where it went with the same sentence.

### Re-walk summary, `07-rewalk-summary.png`

- **Header:** "6 signed off. 3 still open. 1 needs a look." and a segmented bar (signed off, still open, needs a look), with an accessible label.
- **The flagged item comes first,** on a Hold-tinted card (no left border):
  - "Needs a look" label, tape tab, title
  - "Marked fixed on the re-walk, but the defect is still visible."
  - (the inspector pointed at the spot again on the re-walk; the after photo has the same framing and its own blue dot)
  - before and after photos side by side, with dates
  - buttons "Approve and send to <company>" (approves the item and sends it on the trade's channel; the toast says where it went) and "Open item"
- **Then a "Signed off N" list:** rows with a check badge on the tab, the title, and "Signed off" in green text.
- **Then still-open items,** each with "Send again".

### How this walk was processed, `08-how-processed.png`

- Title, then one line: "Done in 1 min 52 s for about $0.41, using three Meta models" (real numbers from the model-call log).
- A step list with uniform graphite markers. The SAM step is "Follow the hand to each defect" ("11 points found across the whole video"). Each step has its name, a model chip ("Muse Voice Transcribe", "SAM 3.1", "Muse Spark Open on Baseten"), a plain detail line, and its duration.
- "On our labeled test walks": defects found, right trade, false "fixed" claims caught, from `eval/report.md`. Never invent these numbers; hide the section if there's no report.

### Landing page, `09-landing-desktop.png`

- **Desktop (≥ 1024 px):**
  - **Header:** the logo (a tape-tab strip with a graphite check mark) and the "Punchwalk" wordmark (Expanded bold) on the left; a "How it works" link on the right.
  - **Left column:**
    - the headline on two deliberate lines, "Walk it once." / "Get a punch list with proof." (56 / 60, Expanded)
    - one paragraph
    - three numbers from `eval/report.md`, each with a 2 px Graphite rule above
    - buttons "Open the demo project" (primary) and "Upload your own walk"
    - a QR code labeled "Open on your phone"
    - the line "Built with Muse Voice Transcribe, SAM 3.1 and Muse Spark Open on Baseten during the Meta Global AI Developer Hackathon, Nov 3–4, 2026."
  - **Right column:** a phone frame (a generic rounded device outline, no brand marks) with the live app running inside an iframe at 393 × 852.
- **Phone:** goes straight into the demo project, with a one-line banner.

## Words

- **Statuses:** Open, Needs a look, Signed off. **Severities:** Cosmetic, Functional, Safety.
- **Trades:** Electrical, Plumbing, Drywall, Painting, Carpentry & millwork, Doors & hardware, Flooring, Windows & glazing, HVAC, Appliances, Cleaning, General.
- **Buttons say what happens:** "Play this moment", "Post 2 items to Slack", "Email 2 items to Brightline Painting", "Copy a link for 2 items", "Upload walk", "Sign off", "Approve and send to Ace Electric".
- **Sending:** To review, To send, Sent (see "Trades"). Channels are named by what they do: "Posts to #punch-electrical.", "Posts to your team's Slack channel.", "Emails maria.chen@example.com.", "Copy a link to share." Never "Outbox". History and toasts say what happened: "Posted 2 items to #punch-electrical", "Emailed 2 items to Brightline Painting", "Copied a link for 3 items".
- **Errors say what happened and what to do, without apologizing.** For example: "This walk is longer than 10 minutes. Split it into parts and upload them together."
- No meta strings joined with dots. Write sentences ("Electrical. 3 open items.") or separate elements.

## PWA (so the demo runs full-screen with no Safari bar)

- `manifest.webmanifest`:
  - name and short_name "Punchwalk"
  - display "standalone", start_url "/app"
  - background color Primer, theme color Sheet
  - icons at 192 and 512 px, plus a maskable icon. The icon is the tape-tab strip with a graphite check on Primer.
- Add an `apple-touch-icon` at 180 px.
- Add `<meta name="apple-mobile-web-app-capable" content="yes">`, set the status bar style to "default", and use `viewport-fit=cover`.
- Apply `env(safe-area-inset-*)` to the top bar, the tab bar and the bottom action bar. Never draw a fake status bar.

## Quality floor

- Text contrast passes AA. Focus is visible. Touch targets are at least 44 px. `prefers-reduced-motion` is respected. Nothing is cut off by the notch or the home indicator.
- Video uses `playsinline` and works with iOS Safari range requests.
- Skeletons while loading. Never show a spinner without saying what's happening.

## Don'ts (these make an app look generic)

- Identical rounded cards with the same grey shadow everywhere; gradient washes; colored left-border accents; glassy blur on everything.
- All-caps labels, small labels above headings, meta strings joined with dots, monospace data labels, arrows tacked onto button text.
- Emoji in the UI, stock illustrations, decorative icons that carry no meaning.
- Talking about tape anywhere in the UI. Users point; nothing is stuck on the wall. Copy says "point", "where you pointed", "Following your hand".
- Burned-in text or numbers on evidence images (the UI draws the tab), or a box around the hand on photos.
- Color as the only signal.

## Screenshot checklist (`make screenshots`: WebKit, iPhone 15 Pro)

1. Punch list: To do (default); filtered (Electrical + Safety); no filter results; first run; To do empty (thumbnails show the red outline + blue dot)
2. Filter sheet open (Trade)
3. Item detail: open item (outlines on, then off); needs-a-look item (photo doesn't show the defect); signed-off item with before/after; the More sheet
4. Play this moment, mid-play with the caption visible
5. Walks list (one processing, one ready)
6. Walk detail mid-play while pointing (hand outline + number tab at the fingertip), with the new-item card over the video; and scrolled, with the mini-player
7. Record: clips added; processing checklist mid-run; done
8. Trades list (with an "All caught up" group); trade screen with items to review; trade screen with only items to send; the sent toast
9. Re-walk summary
10. How this walk was processed
11. Landing page at 1440 × 900 (phone frame + QR) and at 393 × 852

Compare each one with the matching render in `docs/design/`.
