# Screenshots

iPhone 15 Pro (393 × 852 at 2×) in Playwright WebKit, plus one desktop shot at 1440 × 900. All but one
come from the **real demo project**: the live build served by the API, seeded with the recorded walks
(`prj_demo`: `wlk_demo_walk1` first walk, `wlk_demo_walk2` re-walk, `wlk_demo_walk3`). The footage, the
transcript quotes, the defect outlines (red) and the "pointed here" dots (blue) are what the pipeline
produced.

| File | Screen | Data |
|---|---|---|
| `01-punch-list.png` | Punch list, To do: Needs a look first, then rooms | real |
| `02-item-detail.png` | Item 11, outlet cover loose and crooked: before/after slider (red outline + blue dot on the before photo), the action, the inspector's quote, details | real |
| `03-play-this-moment.png` | Play this moment, mid-play: hand outlined, tab 6 at the fingertip, quote as caption | real |
| `04-walk-detail.png` | First walk, mid-play at item 6: hand outline + tab, the "New" card over the video | real |
| `05-record-processing-mock.png` | Record: the live processing checklist mid-run | **mock** (no walk is processing in the real data) |
| `06-rewalk-summary.png` | Re-walk: "7 signed off. 2 still open. 3 need a look." and the stovetop false-fix card | real |
| `07-how-processed.png` | How the first walk was processed: the three Meta models, calls per step | real (replayed from recorded model answers, so it shows no new cost) |
| `08-landing-desktop.png` | Landing page with the live app in the phone frame and the QR code | real |
| `09-trade-preview.png` | Brightline Painting: the message exactly as it will be sent | real |

`09` shows Brightline Painting because Ace Electric has no open items in the real data.

To regenerate: seed the demo project, `pnpm -C web build`, start the API, then capture with Playwright
WebKit (`devices['iPhone 15 Pro']`, viewport 393 × 852, scale 2). The full design-review set, on mock data,
comes from `make screenshots` (see `web/screenshots/README.md`).
