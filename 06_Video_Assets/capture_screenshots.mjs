/**
 * Capture the README screenshots from the real pages.
 *
 * Same idea as capture_frames.mjs, only it stops at stills: it fills in the
 * October 2008 scenario, runs the retrieval, and shoots each section as its
 * own element screenshot so the images stay readable at README width.
 *
 *   python3 -m http.server 8810 --bind 127.0.0.1 &
 *   node capture_screenshots.mjs
 *
 * Writes ../docs/screenshots/*.png.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const BASE = 'http://127.0.0.1:8810';
const OUT = new URL('../docs/screenshots/', import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });

// October 2008: inflation still elevated on paper, unemployment climbing fast,
// credit spreads blown out - the case where the headline number and the
// forward-looking evidence point in opposite directions.
const SCENARIO = {
  cpi_yoy: 4.9,
  unemployment_level: 6.1,
  unemployment_12m_change: 1.4,
  payroll_yoy: -0.8,
  credit_spread_baa10y: 3.45,
  yield_curve_10y_2y: 1.6,
  policy_midpoint: 2.0,
};
const NOTES =
  'Oil prices have declined markedly and inflationary pressures have started ' +
  'to moderate; the intensification of financial market turmoil has augmented ' +
  'the downside risks to growth.';

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 950 },
  deviceScaleFactor: 2,
});

const app = await ctx.newPage();
await app.goto(`${BASE}/FOMC_RAG_Vote_Simulator.html`);
await app.waitForTimeout(1200);

for (const [id, value] of Object.entries(SCENARIO)) {
  await app.fill(`#${id}`, String(value));
}
await app.fill('#notes', NOTES);

await app.click('#run');
await app.waitForSelector('#proposalCard:not([hidden])');
await app.waitForTimeout(1200);

const shoot = async (name, locator) => {
  await locator.screenshot({ path: OUT + name });
  console.log(name);
};

/**
 * Shoot a section but stop after `maxH` CSS pixels. The vote table and the
 * evidence list run thousands of pixels tall; a README wants the top of each,
 * not the whole scroll.
 */
const shootTop = async (name, selector, maxH) => {
  await app.evaluate((s) => document.querySelector(s)
    .scrollIntoView({ block: 'start' }), selector);
  await app.waitForTimeout(300);
  const box = await app.locator(selector).boundingBox();
  const y = Math.max(box.y, 0);
  await app.screenshot({
    path: OUT + name,
    clip: { x: box.x, y, width: box.width, height: Math.min(box.height, maxH) },
  });
  console.log(name);
};

const card = (n) => app.locator('section.card').nth(n);

await shoot('01-scenario.png', app.locator('main > .grid').first());
await shoot('02-direction.png', card(2));
await shootTop('03-votes.png', '#proposalCard', 900);

// Open one member's row so the screenshot shows the per-member evidence that
// the vote actually rests on, not just the summary line.
await app.click('tr.mem[data-row="1"]');
await app.waitForTimeout(600);
await shootTop('04-vote-detail.png', '#proposalCard', 900);

// The committee-level evidence list ships collapsed; open it before shooting.
await app.click('#evToggle');
await app.waitForTimeout(600);
await shootTop('05-evidence.png', '#evidenceCard', 900);

// The scene is a design canvas: its artboard is laid out for a 1600x900
// viewport, so give it its own context and shoot the artboard iframe alone -
// no editor chrome, exactly 1515x852 (16:9).
const sceneCtx = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  deviceScaleFactor: 2,
});
const scene = await sceneCtx.newPage();
await scene.goto(`${BASE}/05_Design_Canvas/fomc-meeting-scene.html`);

// The artboard mounts in a sandboxed iframe seconds after load; wait for all
// twelve seats before shooting or the frame comes back blank.
let board;
for (let i = 0; i < 40 && !board; i++) {
  for (const f of scene.frames()) {
    if (await f.locator('.seat').count().catch(() => 0) >= 12) { board = f; break; }
  }
  if (!board) await scene.waitForTimeout(500);
}
if (!board) throw new Error('artboard never mounted');
await scene.waitForTimeout(9000);   // let the seating animation settle

await scene.locator('iframe').first().screenshot({ path: OUT + '06-meeting-scene.png' });
console.log('06-meeting-scene.png');

// One committee member selected, so the panel shows how a single vote is
// argued rather than sitting empty.
await board.locator('.seat', { hasText: 'Kashkari' }).first().click();
await scene.waitForTimeout(1200);
await scene.locator('iframe').first().screenshot({ path: OUT + '07-member-detail.png' });
console.log('07-member-detail.png');

await browser.close();
