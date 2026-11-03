# Screenshots

`make screenshots` (= `pnpm -C web screenshots`) builds the app in mock mode, serves it with
`vite preview` (no backend), and captures every screen in DESIGN.md's checklist with Playwright WebKit:

- phone: `devices['iPhone 15 Pro']` with the viewport set to the full 393 × 852 and 2× scale (same as the
  renders in `docs/design/`)
- desktop: 1440 × 900 at 1×

Output goes to `.verify/screens/<nn>-<name>.png`, plus `index.html`, a contact sheet that puts each shot
next to its reviewed render and lists console errors and failed setup steps.

```sh
pnpm -C web screenshots                    # everything (clears old PNGs first)
pnpm -C web exec node screenshots/run.ts punch item   # only names containing "punch" or "item"
SCREENS_STRICT=1 pnpm -C web screenshots   # a failed setup step fails the run
```

The run exits non-zero if a page fails to load. Until a screen exists, its `setup` step fails softly
(it's noted in the log and on the contact sheet), so placeholders still get a screenshot. Once your
screen is built, run with `SCREENS_STRICT=1` to prove the setup really works.

## Adding or extending a screen

Every screen is one entry in `SCREENS` in `screens.ts`:

```ts
{
  name: 'filter-sheet-trade',            // file: <nn>-filter-sheet-trade.png (nn = position in the list)
  checklist: '2. Filter sheet open (Trade)',
  path: '/',                              // may include ?mock_* switches, see below
  render: '01-punch-list.png',            // optional: the docs/design render to compare with
  fullPage: true,                         // optional: capture the whole scroll height
  viewport: 'desktop',                    // optional: default 'phone'
  setup: async (page) => {                // optional: get the screen into the state to capture
    await tapButton(page, /^Trade/);
    await sheetOpen(page);
  },
}
```

Helpers in `screens.ts`: `tap` (button or link by accessible name), `tapButton` (buttons only, so
"Trade" can't hit the Trades tab), `sheetOpen` (waits for a vaul sheet to finish sliding up),
`seekVideo(page, t, play?)`, `scrollBy(page, y)` (mobile WebKit has no mouse wheel), and
`addClips(page, names)` (fake files on the first file input). Prefer accessible names over CSS
selectors, so the setup also checks the screen is labeled.

Keep the order of the list the same as DESIGN.md's checklist; add new entries next to the related ones.
Inserting an entry renumbers the files after it, which is fine since the folder is rebuilt every run.

## Mock data switches

The mock adapter (`src/api/mock.ts`) reads these from the URL and keeps them for the browser session:

| Query | Effect |
|---|---|
| `?mock_scenario=first_run` | no walks, no items (first-run empty state) |
| `?mock_scenario=todo_empty` | every item signed off ("Everything is signed off.") |
| `?mock_scenario=offline` | every call fails as if the server were down |
| `?mock_progress=5` | pin the processing replay to `walks/wlk_3a_first/progress/05.json` (1–8) |
| `?mock_latency=300` | delay every answer, to see skeletons |
| `?mock_reset=1` | clear the switches |

In a setup step you can also drive it directly: `await page.evaluate(() => window.__punchwalkMock.setProgressStep(3))`.
Each screen gets a fresh browser context, so writes from one screen (a sign-off, a send) never leak into the next.
