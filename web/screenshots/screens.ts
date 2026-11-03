// Every screen in DESIGN.md's "Screenshot checklist", in order. `make screenshots` writes each one to
// .verify/screens/<nn>-<name>.png. See README.md in this folder for how to add or extend a screen.
import type { Page } from '@playwright/test';

export type Viewport = 'phone' | 'desktop';

export interface Screen {
  /** Kebab-case; the file is <nn>-<name>.png. */
  name: string;
  /** DESIGN.md checklist entry this covers ("1. Punch list: To do (default)"). */
  checklist: string;
  /** App path, including any ?mock_* switches (see src/api/mock.ts). */
  path: string;
  /** Default phone (iPhone 15 Pro, 393 x 852). */
  viewport?: Viewport;
  /** Capture the whole scroll height instead of one viewport. */
  fullPage?: boolean;
  /** The reviewed render to compare with, in docs/design/. */
  render?: string;
  /** Interactions after load: open a sheet, apply a filter, seek the video... Best effort until the screen exists. */
  setup?: (page: Page) => Promise<void>;
}

// ------------------------------------------------------------------ setup helpers

/** Clicks the first visible element with this accessible name (button or link). */
export async function tap(page: Page, name: string | RegExp) {
  const target = page.getByRole('button', { name }).or(page.getByRole('link', { name })).first();
  await target.click({ timeout: 3000 });
}

/** Clicks the first button (never a link, so "Trade" can't hit the Trades tab). */
export async function tapButton(page: Page, name: string | RegExp) {
  await page.getByRole('button', { name }).first().click({ timeout: 3000 });
}

/** Scrolls the page (mobile WebKit has no mouse wheel). */
export async function scrollBy(page: Page, y: number) {
  await page.evaluate((dy) => window.scrollBy(0, dy), y);
  await page.waitForTimeout(300);
}

/** Waits for the open sheet (vaul) to finish sliding up. */
export async function sheetOpen(page: Page) {
  await page.getByRole('dialog').first().waitFor({ state: 'visible', timeout: 3000 });
  await page.waitForTimeout(450);
}

/** Seeks the first <video> to t seconds and plays/pauses as asked. */
export async function seekVideo(page: Page, t: number, play = false) {
  await page.locator('video').first().waitFor({ timeout: 5000 });
  await page.evaluate(
    async ({ t, play }) => {
      const v = document.querySelector('video');
      if (!v) throw new Error('no video');
      if (v.readyState < 1) await new Promise((r) => v.addEventListener('loadedmetadata', r, { once: true }));
      v.muted = true;
      v.currentTime = t;
      await new Promise((r) => v.addEventListener('seeked', r, { once: true }));
      if (play) await v.play();
      else v.pause();
    },
    { t, play },
  );
  await page.waitForTimeout(300);
}

/** Attaches fake clip files to the first file input (the Record screen). */
export async function addClips(page: Page, names: string[]) {
  const input = page.locator('input[type=file]').first();
  await input.setInputFiles(
    names.map((name) => ({ name, mimeType: 'video/mp4', buffer: Buffer.from('mock clip') })),
    { timeout: 3000 },
  );
}

// ------------------------------------------------------------------ the checklist

export const SCREENS: Screen[] = [
  // 1. Punch list
  { name: 'punch-list-todo', checklist: '1. Punch list: To do (default; thumbnails show the red outline + blue dot)', path: '/', render: '01-punch-list.png' },
  {
    name: 'punch-list-filtered',
    checklist: '1. Punch list: filtered (Electrical + Safety)',
    path: '/?trade=electrical&severity=safety',
    render: '01-punch-list.png',
  },
  {
    name: 'punch-list-no-results',
    checklist: '1. Punch list: no filter results',
    path: '/?q=chandelier',
  },
  {
    name: 'punch-list-first-run',
    checklist: '1. Punch list: first run',
    path: '/?mock_scenario=first_run',
  },
  {
    name: 'punch-list-todo-empty',
    checklist: '1. Punch list: To do empty',
    path: '/?mock_scenario=todo_empty',
  },
  // 2. Filter sheet
  {
    name: 'filter-sheet-trade',
    checklist: '2. Filter sheet open (Trade)',
    path: '/',
    setup: async (page) => {
      await tapButton(page, /^Trade/);
      await sheetOpen(page);
      await page.getByRole('checkbox', { name: /Electrical/ }).check();
    },
  },
  // 3. Item detail
  {
    name: 'item-open',
    checklist: '3. Item detail: open item (outlines on)',
    path: '/items/itm_2b_07',
    render: '02-item-detail.png',
  },
  {
    name: 'item-open-details',
    checklist: '3. Item detail: open item, scrolled to Details, Checked and History',
    path: '/items/itm_2b_07',
    setup: async (page) => {
      await scrollBy(page, 5000);
    },
  },
  {
    name: 'item-open-outlines-off',
    checklist: '3. Item detail: open item (outlines off: the plain frame, no outline, no dot)',
    path: '/items/itm_2b_07',
    setup: async (page) => {
      await tapButton(page, /^Outlines on/);
      await page.waitForTimeout(300);
    },
  },
  {
    // The needs-a-look item whose photo doesn't show the defect (REVIEW_NOT_IN_FRAME). itm_2b_12 in the
    // scenario; check fixtures/mock_api/README.md if the scenario is regenerated.
    name: 'item-needs-look',
    checklist: '3. Item detail: needs-a-look item (photo doesn\'t show the defect)',
    path: '/items/itm_2b_12',
  },
  {
    name: 'item-signed-off',
    checklist: '3. Item detail: signed-off item with before/after',
    path: '/items/itm_2b_01',
  },
  {
    name: 'item-more-sheet',
    checklist: '3. Item detail: the More sheet',
    path: '/items/itm_2b_07',
    setup: async (page) => {
      await tapButton(page, 'More actions');
      await sheetOpen(page);
    },
  },
  // 4. Play this moment
  {
    name: 'play-this-moment',
    checklist: '4. Play this moment, mid-play with the caption visible',
    path: '/items/itm_2b_01',
    render: '03-play-this-moment.png',
    setup: async (page) => {
      await tapButton(page, /Play this moment/);
      await sheetOpen(page);
      // Mid-moment, paused, inside the quote: the caption is on screen and the frame is deterministic.
      await seekVideo(page, 15.2);
    },
  },
  // 5. Walks list
  { name: 'walks-list', checklist: '5. Walks list (one processing, one ready)', path: '/walks' },
  // 6. Walk detail
  {
    name: 'walk-detail-new-item',
    checklist: '6. Walk detail mid-play while pointing (hand outline + number tab at the fingertip), with the new-item card',
    path: '/walks/wlk_2b_first',
    render: '04-walk-detail.png',
    setup: async (page) => {
      await seekVideo(page, 92);
      await page.waitForTimeout(400);
    },
  },
  {
    name: 'walk-detail-mini-player',
    checklist: '6. Walk detail scrolled, with the mini-player',
    path: '/walks/wlk_2b_first',
    setup: async (page) => {
      await scrollBy(page, 700);
    },
  },
  // 7. Record
  {
    name: 'record-clips-added',
    checklist: '7. Record: clips added',
    path: '/record',
    setup: async (page) => {
      await addClips(page, ['walk1_a.mp4', 'walk1_b.mp4']);
    },
  },
  {
    name: 'record-processing',
    checklist: '7. Record: processing checklist mid-run',
    path: '/record?walk=wlk_3a_first&mock_progress=5',
    render: '05-record-processing.png',
  },
  {
    name: 'record-done',
    checklist: '7. Record: done',
    path: '/record?walk=wlk_3a_first&mock_progress=8',
  },
  // 8. Trades
  { name: 'trades-list', checklist: '8. Trades list', path: '/trades' },
  {
    name: 'trade-preview',
    checklist: '8. Trade screen with message preview',
    path: '/trades/electrical',
    fullPage: true,
    render: '06-send-to-trade.png',
  },
  {
    name: 'trade-sent-toast',
    checklist: '8. Sent toast',
    path: '/trades/electrical',
    setup: async (page) => {
      await tapButton(page, /^Send \d+ items? to/);
      await page.getByRole('status').filter({ hasText: /^Sent/ }).first().waitFor({ timeout: 3000 });
    },
  },
  // 9. Re-walk summary
  {
    name: 'rewalk-summary',
    checklist: '9. Re-walk summary',
    path: '/walks/wlk_2b_rewalk/rewalk',
    fullPage: true,
    render: '07-rewalk-summary.png',
  },
  // 10. How this walk was processed
  {
    name: 'how-processed',
    checklist: '10. How this walk was processed',
    path: '/walks/wlk_2b_first/processing',
    fullPage: true,
    render: '08-how-processed.png',
  },
  // 11. Landing page
  {
    name: 'landing-desktop',
    checklist: '11. Landing page at 1440 x 900 (phone frame + QR)',
    path: '/landing',
    viewport: 'desktop',
    render: '09-landing-desktop.png',
  },
  { name: 'landing-phone', checklist: '11. Landing page at 393 x 852', path: '/landing' },
  // Design-system check (PLAN 5.1 AC) and extras
  { name: 'dev-components', checklist: 'Component gallery (5.1)', path: '/dev/components', fullPage: true },
  {
    name: 'dev-components-sheet',
    checklist: 'Component gallery: filter sheet open (5.1)',
    path: '/dev/components',
    setup: async (page) => {
      await tapButton(page, 'Open filter sheet');
      await sheetOpen(page);
    },
  },
  {
    name: 'dev-components-desktop',
    checklist: 'Desktop: the app in a centered 440 px column',
    path: '/dev/components',
    viewport: 'desktop',
  },
  { name: 'share-item', checklist: 'Public share page (6.1)', path: '/share/item/shr_e33ec5bd7e' },
];
