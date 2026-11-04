# Screenshots

iPhone 15 Pro (393 × 852 at 2×) in Playwright WebKit, plus one desktop shot at 1440 × 900. All but one
come from the **real demo project**: the live build served by the API, seeded with the recorded walks
(`prj_demo`: `wlk_demo_walk1` first walk, `wlk_demo_walk2` re-walk, `wlk_demo_walk3`), after it was
re-grounded with grounding v2 (Nov 4). The footage, the transcript quotes, the defect outlines (red) and
the "pointed here" dots (blue) are what the pipeline produced. Items without a trustworthy outline show only
the dot (or nothing, for the voice-only #004).

| File | Screen | Data |
|---|---|---|
| `01-punch-list.png` | Punch list, To do 7: Needs a look 3 first (#003 with its outline on the burnt burner ring, #004, #012), then rooms | real |
| `02-item-detail.png` | Item 11, outlet cover loose and crooked: before/after slider dragged to show the before photo (red outline on the crooked cover plate + blue dot), the action, the inspector's quote, details | real |
| `03-play-this-moment.png` | Play this moment for item 6, mid-play at 0:44: hand outlined, tab 6 at the fingertip, quote as caption | real |
| `04-walk-detail.png` | First walk, mid-play at 0:44 (item 6): hand outline + tab, the "New" card over the video | real |
| `05-record-processing-mock.png` | Record: the live processing checklist mid-run | **mock** (no walk is processing in the real data) |
| `06-rewalk-summary.png` | Re-walk: "7 signed off. 2 still open. 3 need a look." and the #003 stovetop false-fix card (before with the outline on the burnt ring, after, "Approve and send to Appliances"), then #004 | real |
| `07-how-processed.png` | How the first walk was processed: the three Meta models, calls per step | real (replayed from recorded model answers, so it shows no new cost) |
| `08-landing-desktop.png` | Landing page (1440 × 900) with the app in the phone frame and the QR code | real |
| `09-trade-preview.png` | Brightline Painting: "1 to review, 2 to send.", the review row for #012, Slack ("Posts to your team's Slack channel.") or Copy link, the message exactly as it will be sent, "Post 2 items to Slack" | real |

`09` shows Brightline Painting because its #009 and #010 are the demo's To send items (Ace Electric has only
one to review). For the capture, the team Slack webhook was set to a placeholder that can't be reached, so
the screen matches the live app (where the team webhook is configured). Nothing was sent.

To regenerate: seed the demo project (or copy a re-grounded `data/`), `pnpm -C web build`, start the API
with `PUNCHWALK_WORKER=0 AUTO_SEND=false PUNCHWALK_CACHE=readonly`, then capture with Playwright
WebKit (`devices['iPhone 15 Pro']`, viewport 393 × 852, scale 2). The full design-review set, on mock data,
comes from `make screenshots` (see `web/screenshots/README.md`).
