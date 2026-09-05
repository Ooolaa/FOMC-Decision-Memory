/**
 * Drive the real demo and capture video frames with per-frame durations.
 *
 * The shot list mirrors VIDEO_SCRIPT_zh-TW.md: every section's total duration
 * equals that section's measured narration length plus the 2.5s gap, so the
 * frames line up with the narration audio without any manual trimming.
 *
 * Writes frames/NNNN.jpg plus manifest.json ({file, dur} in order).
 *
 *   node capture_frames.mjs            # full run
 *   node capture_frames.mjs --fast     # low-res proof that the shot list works
 */
import { chromium } from 'playwright';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';

const BASE = 'http://127.0.0.1:8810';
const APP = `${BASE}/FOMC_RAG_Vote_Simulator.html`;
const SCENE = `${BASE}/05_Design_Canvas/fomc-meeting-scene.html`;
const FAST = process.argv.includes('--fast');

// Section boundaries come from the measured narration, never from numbers
// typed in here: build_narration.py times every sentence and writes them out,
// so a re-recorded voice track moves the camera with it.
const narration = JSON.parse(readFileSync(new URL('./narration.json', import.meta.url)));
const ends = narration.sections.map((_, i) =>
  i + 1 < narration.sections.length ? narration.sections[i + 1].start : narration.total);
const END = (i) => ends[i - 1];

// The scene is a design canvas: clip away the editor toolbar so the video
// shows only the artboard (measured at 1515x852, exactly 16:9).
const SCENE_CLIP = { x: 43, y: 48, width: 1515, height: 852 };

const OUT = new URL('./frames/', import.meta.url).pathname;
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const manifest = [];
let n = 0;

async function shoot(page, dur, clip) {
  const file = `${String(n++).padStart(4, '0')}.jpg`;
  await page.screenshot({
    path: OUT + file,
    type: 'jpeg',
    quality: FAST ? 60 : 92,
    ...(clip ? { clip } : {}),
  });
  manifest.push({ file, dur });
  return file;
}

/** One frame held for `sec` seconds. */
const hold = (page, sec, clip) => shoot(page, sec, clip);

/** Scroll from y0 to y1 across `sec` seconds, easing in and out. */
async function scrollTo(page, y0, y1, sec, clip) {
  const fps = FAST ? 4 : 10;
  const frames = Math.max(2, Math.round(sec * fps));
  for (let i = 1; i <= frames; i++) {
    const t = i / frames;
    const e = t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t);   // easeInOut
    await page.evaluate((y) => window.scrollTo(0, y), Math.round(y0 + (y1 - y0) * e));
    await shoot(page, sec / frames, clip);
  }
}

/**
 * Capture live motion. Screenshots are far slower than the animation, so each
 * frame carries the time that actually elapsed while taking it - playback then
 * runs at true speed (choppy, but never sped up or slowed down).
 */
async function live(page, sec, clip) {
  const end = Date.now() + sec * 1000;
  let prev = Date.now();
  while (Date.now() < end) {
    await shoot(page, 0, clip);
    const now = Date.now();
    manifest[manifest.length - 1].dur = (now - prev) / 1000;
    prev = now;
  }
}

const total = () => manifest.reduce((a, f) => a + f.dur, 0);

/** Pad the section out to its exact budget with one still frame. */
async function fill(page, target, clip) {
  const gap = target - total();
  if (gap > 0.05) await hold(page, gap, clip);
  else if (gap < -0.5) console.warn(`  ! over budget by ${(-gap).toFixed(1)}s`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  deviceScaleFactor: FAST ? 1 : 1.2,
});
const app = await ctx.newPage();
const scene = await ctx.newPage();

await scene.goto(SCENE);
await app.goto(APP);
await app.waitForTimeout(1200);

// The artboard mounts inside a sandboxed iframe a few seconds after load;
// capturing before that yields blank frames. Preload it now so it is ready
// long before shot 5, and drive it through Playwright's frame API.
const artboard = async () => {
  for (let i = 0; i < 40; i++) {
    for (const f of scene.frames()) {
      if (await f.locator('.seat').count().catch(() => 0) >= 12) return f;
    }
    await scene.waitForTimeout(500);
  }
  throw new Error('artboard never mounted');
};
const board = await artboard();
console.log('artboard ready');

// Warm the index so the first click in shot 3 is instant, then reset the view.
await app.click('#run');
await app.waitForTimeout(1500);
await app.evaluate(() => window.scrollTo(0, 0));
await app.waitForTimeout(400);

// ---- 1. 情境 --------------------------------------------------
console.log('S1 情境');
await fill(app, END(1));

// ---- 2. 決策記憶 --------------------------------------------------
console.log('S2 決策記憶');
await scrollTo(app, 0, 300, 2.5);
await hold(app, 11);
await scrollTo(app, 300, 620, 2);
await fill(app, END(2));

// ---- 3. 方向 --------------------------------------------------
console.log('S3 方向');
await scrollTo(app, 620, 430, 1.2);
await app.click('#run');
await app.waitForTimeout(250);
await hold(app, 1.5);
await scrollTo(app, 430, 1180, 2.4);
await hold(app, 15);
await scrollTo(app, 1180, 1470, 1.6);
await fill(app, END(3));

// ---- 4. 逐一委員 --------------------------------------------------
console.log('S4 逐一委員');
await scrollTo(app, 1470, 2080, 2.2);
await hold(app, 7);
await app.click('tr.mem[data-row="1"]');          // Beth M. Hammack
await live(app, 0.9);
await hold(app, 13);
await app.click('tr.mem[data-row="3"]');          // Jerome H. Powell
await live(app, 0.9);
await fill(app, END(4));

// ---- 5. 會議現場 --------------------------------------------------
console.log('S5 會議現場');
await scrollTo(app, 2080, 3400, 2.2);
await hold(app, 2.2);
await scene.bringToFront();
await board.locator('.replay').click();            // restart the animation at 0
await live(scene, 4.5, SCENE_CLIP);
await hold(scene, 9, SCENE_CLIP);
await board.locator('.seat', { hasText: 'Kashkari' }).first().click();
await live(scene, 0.8, SCENE_CLIP);
await fill(scene, END(5), SCENE_CLIP);

// ---- 6. 邊界 --------------------------------------------------
console.log('S6 邊界');
await app.bringToFront();
await app.evaluate(() => window.scrollTo(0, 1180));
await app.waitForTimeout(300);
await fill(app, END(6));

await browser.close();
writeFileSync(new URL('./manifest.json', import.meta.url), JSON.stringify(manifest, null, 1));
console.log(`\n${manifest.length} frames, ${total().toFixed(1)}s total`);
