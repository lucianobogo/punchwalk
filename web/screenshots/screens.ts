// Every screen in DESIGN.md's "Screenshot checklist", in order. `make screenshots` writes each one to
// .verify/screens/<nn>-<name>.png. See README.md in this folder for how to add or extend a screen.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page } from '@playwright/test';

const FIXTURE_MEDIA = resolve(dirname(fileURLToPath(import.meta.url)), '../../fixtures/mock_api/media');

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

/**
 * itm_2b_12 (general trade, needs a look) as the live app serves #003: the trade's company is
 * "Northline Appliance Service" and History carries the field-less `edited` entry the pipeline writes.
 */
export async function routeLongLabelItem(page: Page) {
  await page.route('**/mock-api/projects/prj_unit2b.json', async (route) => {
    const project = await (await route.fetch()).json();
    project.trades = project.trades.map((t: { trade: string }) => (t.trade === 'general' ? { ...t, company: 'Northline Appliance Service' } : t));
    await route.fulfill({ json: project });
  });
  await page.route('**/mock-api/items/itm_2b_12.json', async (route) => {
    const item = await (await route.fetch()).json();
    const [created, ...rest] = item.history;
    const wrote = { at: created.at, actor: 'model', action: 'edited', field: null, from: null, to: null, note: 'Muse Spark Open wrote the item' };
    item.history = [created, wrote, ...rest];
    await route.fulfill({ json: item });
  });
  await page.reload({ waitUntil: 'networkidle' });
}

/**
 * Attaches clips to the first file input (the Record screen). Each clip is a real mock video
 * (fixtures/mock_api/media/<path>) under the given file name, so thumbnails and lengths load.
 */
export async function addClips(page: Page, clips: { name: string; media: string }[]) {
  const input = page.locator('input[type=file]').first();
  await input.setInputFiles(
    clips.map((c) => ({ name: c.name, mimeType: 'video/mp4', buffer: readFileSync(resolve(FIXTURE_MEDIA, c.media)) })),
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
  {
    name: 'punch-list-offline',
    checklist: '8.4 Error state: the API doesn’t answer (calm card with Try again)',
    path: '/?mock_scenario=offline',
    setup: async (page) => {
      await page.getByText('This didn’t load.').waitFor({ timeout: 6000 });
    },
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
    // Item 5 is signed off on the re-walk. Its original check is turned inconsistent here (as in the demo
    // data, where the pinned frame was never re-picked): the stale note must not show, and nothing gaps.
    name: 'item-signed-off-stale-check',
    checklist: '3. Item detail: signed-off item whose original check said the frame doesn\'t show the defect (no Checked note), scrolled down',
    path: '/items/itm_2b_05',
    setup: async (page) => {
      await page.route('**/mock-api/items/itm_2b_05.json', async (route) => {
        const item = await (await route.fetch()).json();
        item.verification = { ...item.verification, consistent: false, note: "The frame doesn't show the defect described.", issues: [] };
        await route.fulfill({ json: item });
      });
      await page.reload({ waitUntil: 'networkidle' });
      await page.getByRole('button', { name: 'Reopen' }).waitFor({ timeout: 4000 });
      await scrollBy(page, 5000);
    },
  },
  {
    name: 'item-needs-look-details',
    checklist: '3. Item detail: needs-a-look item, scrolled to Details, Checked (the verifier\'s note) and History',
    path: '/items/itm_2b_12',
    setup: async (page) => {
      await scrollBy(page, 5000);
    },
  },
  {
    // The longest real company name in the demo ("Approve and send to Northline Appliance Service", 47
    // characters): the primary button wraps to two lines beside the More button, never clips.
    name: 'item-needs-look-long-label',
    checklist: '3. Item detail: needs-a-look item whose trade has a long company name (primary button wraps)',
    path: '/items/itm_2b_12',
    setup: async (page) => {
      await routeLongLabelItem(page);
      await page.getByRole('button', { name: 'Approve and send to Northline Appliance Service' }).waitFor({ timeout: 4000 });
    },
  },
  {
    // The same item scrolled to History: the field-less "edited" entry the pipeline writes shows its note
    // ("Muse Spark Open wrote the item"), never "Changed the item from nothing to nothing."
    name: 'item-needs-look-long-label-history',
    checklist: '3. Item detail: History with the model\'s whole-item write, and the long-label bottom bar',
    path: '/items/itm_2b_12',
    setup: async (page) => {
      await routeLongLabelItem(page);
      await page.getByRole('button', { name: 'Approve and send to Northline Appliance Service' }).waitFor({ timeout: 4000 });
      await scrollBy(page, 5000);
    },
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
  {
    name: 'item-gone',
    checklist: '8.4 Error state: an item that was dismissed or never existed',
    path: '/items/itm_gone',
    setup: async (page) => {
      await page.getByText('This item isn’t here anymore.').waitFor({ timeout: 4000 });
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
      // Mid-play inside the quote (pause icon showing): start at 14.8 s; the capture lands around 15.5 s,
      // well inside the caption (14.0-17.8 s) and before the moment ends (18.2 s).
      await seekVideo(page, 14.8, true);
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
      // Item 6 "Door doesn't latch" is called out at 92.0 s; the finger holds still 91.6-93.6 s and the
      // card shows until 94.5 s. Play from 92.2 s so the capture (~1 s later) is mid-play with both on screen.
      await seekVideo(page, 92.2, true);
      await page.getByText('New', { exact: true }).waitFor({ timeout: 3000 });
      await page.waitForTimeout(400);
    },
  },
  {
    name: 'walk-detail-mini-player',
    checklist: '6. Walk detail scrolled, with the mini-player',
    path: '/walks/wlk_2b_first',
    setup: async (page) => {
      await seekVideo(page, 112.5);
      await scrollBy(page, 420);
      await page.waitForTimeout(400);
    },
  },
  // 7. Record
  {
    name: 'record-clips-added',
    checklist: '7. Record: clips added',
    path: '/record',
    setup: async (page) => {
      await addClips(page, [
        { name: 'walk1_a.mp4', media: 'walks/wlk_2b_first/display.mp4' },
        { name: 'walk1_b.mp4', media: 'walks/wlk_2b_rewalk/display.mp4' },
      ]);
      await page.getByText(/^\d+:\d\d, /).nth(1).waitFor({ timeout: 8000 });
      await page.waitForTimeout(300);
    },
  },
  {
    name: 'record-processing',
    checklist: '7. Record: processing checklist mid-run',
    // Snapshot 5: "Writing items, 2 of 4 written" with two items so far (closest to the render).
    path: '/record?walk=wlk_3a_first&mock_progress=5',
    render: '05-record-processing.png',
  },
  {
    name: 'record-done',
    checklist: '7. Record: done',
    path: '/record?walk=wlk_3a_first&mock_progress=8',
  },
  // 8. Trades
  {
    name: 'trades-list',
    checklist: '8. Trades list, with an "All caught up" group',
    path: '/trades',
    setup: async (page) => {
      await page.getByRole('heading', { name: 'All caught up' }).waitFor({ timeout: 3000 });
    },
  },
  {
    name: 'trade-preview',
    checklist: '8. Trade screen: Ace Electric (Slack), nothing new to send, Already sent, channel picker',
    path: '/trades/electrical',
    render: '06-send-to-trade.png',
    setup: async (page) => {
      await page.getByRole('heading', { name: 'Already sent' }).waitFor({ timeout: 3000 });
    },
  },
  {
    name: 'trade-to-review',
    checklist: '8. Trade screen with an item to review (amber row opens it)',
    path: '/trades/general',
    setup: async (page) => {
      await page.getByText(/needs your review before it can go out/).waitFor({ timeout: 3000 });
    },
  },
  {
    name: 'trade-to-send',
    checklist: '8. Trade screen with only items to send (Clearview Glass, via the team Slack channel)',
    path: '/trades/windows_glazing',
    setup: async (page) => {
      await page.getByRole('button', { name: /^(Post \d+ items? to Slack|Email \d+ items? to .+|Copy a link for \d+ items?)$/ }).waitFor({ timeout: 3000 });
    },
  },
  {
    name: 'trade-sent-toast',
    checklist: '8. Sent toast ("Posted 1 item to #punch-electrical") and the history row',
    path: '/trades/electrical',
    setup: async (page) => {
      await page.getByRole('checkbox', { name: /^Send item \d+ again$/ }).first().check();
      await tapButton(page, /^Post \d+ items? to Slack$/);
      await page.getByRole('status').filter({ hasText: /^Posted \d+ items? to / }).first().waitFor({ timeout: 3000 });
    },
  },
  // 9. Re-walk summary
  {
    name: 'rewalk-summary',
    checklist: '9. Re-walk summary',
    path: '/walks/wlk_2b_rewalk/rewalk',
    render: '07-rewalk-summary.png',
    setup: async (page) => {
      await page.getByText('Marked fixed on the re-walk, but the defect is still visible.').waitFor({ timeout: 3000 });
    },
  },
  {
    name: 'rewalk-summary-lists',
    checklist: '9. Re-walk summary: signed off, still open with Send again, not checked',
    path: '/walks/wlk_2b_rewalk/rewalk',
    setup: async (page) => {
      await page.evaluate(() => {
        const el = document.querySelector('section[aria-label="Still open"]');
        if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 360);
      });
      await page.waitForTimeout(300);
    },
  },
  // 10. How this walk was processed
  {
    name: 'how-processed',
    checklist: '10. How this walk was processed',
    path: '/walks/wlk_2b_first/processing',
    render: '08-how-processed.png',
  },
  {
    name: 'how-processed-calls',
    checklist: '10. How this walk was processed: eval numbers and every model call',
    path: '/walks/wlk_2b_first/processing',
    setup: async (page) => {
      await tapButton(page, 'Show all');
      await page.evaluate(() => {
        const h = document.getElementById('eval-h') ?? document.getElementById('calls-h');
        if (h) window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 70);
      });
      await page.waitForTimeout(300);
    },
  },
  // 11. Landing page
  {
    name: 'landing-desktop',
    checklist: '11. Landing page at 1440 x 900 (phone frame + QR)',
    path: '/landing',
    viewport: 'desktop',
    render: '09-landing-desktop.png',
    setup: async (page) => {
      // The phone frame runs the real app; wait until it has drawn the punch list.
      await page.frameLocator('iframe').getByRole('heading', { name: 'Unit 2B final punch' }).waitFor({ timeout: 8000 });
      await page.getByRole('img', { name: /^QR code for/ }).waitFor({ timeout: 3000 });
      await page.waitForTimeout(500);
    },
  },
  {
    name: 'landing-phone',
    checklist: '11. Landing page at 393 x 852: straight into the demo project, with the banner',
    path: '/landing',
    setup: async (page) => {
      await page.waitForURL((u) => u.pathname === '/', { timeout: 3000 });
      await page.getByText('Demo project.').waitFor({ timeout: 3000 });
    },
  },
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
  {
    name: 'share-item',
    checklist: 'Public share page (6.1): item 11 as a trade sees it',
    path: '/share/item/shr_33dfa14b0c',
    setup: async (page) => {
      await page.getByRole('button', { name: /Play this moment/ }).waitFor({ timeout: 3000 });
    },
  },
];
