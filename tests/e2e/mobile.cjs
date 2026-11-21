// Mobile test: drives the public and Studio pages in Chromium emulating phones and a tablet (touch, small screens, device pixel ratio).
//   MS_PY=/path/to/python node tests/e2e/mobile.cjs        Screenshots go to $MS_SHOTS if set.
// Chromium is not iOS Safari: this proves layout, touch handling and tap-target sizes, not Safari's own quirks.
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), assert = require("assert");
const { chromium, devices } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8193;
const SHOTS = process.env.MS_SHOTS;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-mobile-"));
const auth = path.join(tmp, "site-auth"), data = path.join(tmp, "data");
const keys = path.join(ROOT, "app", "siteauth", "keys.py");
execFileSync(PY, [keys, "init", "--dir", auth, "--no-chown"]);
execFileSync(PY, [keys, "ensure-app", "--dir", auth, "--app", "musicshare", "--path", "/music-share", "--no-chown"]);
const cookie = execFileSync(PY, ["-c", `
import importlib.util as u, time
sp = u.spec_from_file_location("k", "${keys}"); k = u.module_from_spec(sp); sp.loader.exec_module(k)
import sys; sys.path.insert(0, "${path.join(ROOT, "app")}")
from siteauth import make_token
print(make_token(k.derive(open("${auth}/master").read().strip(), "musicshare", 1), time.time()))`]).toString().trim();
const NAMES = ["A rather long title for a demo that I recorded last Tuesday night at the studio and want you to hear", "Short one", "Third idea", "Fourth idea", "Fifth idea", "Sixth idea"];
NAMES.forEach((n, i) => execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", `sine=frequency=${300 + i * 60}:duration=20`, path.join(tmp, `t${i}.wav`)]));
const server = spawn(PY, ["-m", "waitress", `--listen=127.0.0.1:${PORT}`, "wsgi:app"], { cwd: path.join(ROOT, "app"), stdio: "inherit", env: { ...process.env,
  MUSICSHARE_DATA_DIR: data, MUSICSHARE_BASE_PATH: "/music-share", MUSICSHARE_STATIC_DIR: path.join(ROOT, "static"), MUSICSHARE_SITE_KEY_FILE: path.join(auth, "keys", "musicshare.key"),
  MUSICSHARE_INSECURE_COOKIES: "1", MUSICSHARE_CONFIG: "/nonexistent" } });
const B = `http://127.0.0.1:${PORT}/music-share/`;
const H = { Cookie: "site_musicshare=" + cookie };
const step = (m) => console.log("ok  " + m);
const DEVICES = ["iPhone SE", "iPhone 13", "Pixel 5", "iPhone 13 landscape", "iPad Mini"];

(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(B + "health")).ok) break; } catch (e) {} await new Promise((r) => setTimeout(r, 100)); }
  // set up through the API: six tracks, all listed, titles as above
  const made = [];
  for (let i = 0; i < NAMES.length; i++) {
    const fd = new FormData();
    fd.append("files", new Blob([fs.readFileSync(path.join(tmp, `t${i}.wav`))]), `t${i}.wav`);
    fd.append("visibility", "listed");
    const r = await fetch(B + "api/tracks", { method: "POST", body: fd, headers: H });
    assert.strictEqual(r.status, 201);
    made.push((await r.json()).tracks[0]);
  }
  for (let i = 0; i < made.length; i++) assert.ok((await fetch(B + `api/tracks/${made[i].id}`, { method: "PATCH", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ title: NAMES[i] }) })).ok);
  for (let i = 0; i < 100; i++) {
    const rows = await (await fetch(B + "api/tracks", { headers: H })).json();
    if (rows.every((t) => t.analysis_status === "done" || (t.peaks && t.peaks.length))) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  const first = (await (await fetch(B + "api/tracks", { headers: H })).json())[0];
  const trackUrl = B + "t/" + first.token;
  const browser = await chromium.launch();
  const problems = [];
  const note = (dev, what, bad) => { if (bad) problems.push(`${dev}: ${what}`); };
  try {
    for (const name of DEVICES) {
      const { defaultBrowserType, ...desc } = devices[name];
      const ctx = await browser.newContext(desc);
      const errors = [];
      const open = async (url, owner) => {
        const c = owner ? ctx : await browser.newContext(desc);
        if (owner) await ctx.addCookies([{ name: "site_musicshare", value: cookie, url: B }]);
        const p = await c.newPage();
        p.on("console", (m) => { if (m.type() === "error") errors.push(`${name} console: ${m.text()}`); });
        p.on("pageerror", (e) => errors.push(`${name} pageerror: ${e.message}`));
        await p.goto(url);
        return p;
      };
      const audit = async (p, label) => {
        const r = await p.evaluate(() => {
          const out = { overflow: document.documentElement.scrollWidth - window.innerWidth, small: [], smallText: [], vp: document.querySelector('meta[name=viewport]').content };
          const vis = (e) => { const b = e.getBoundingClientRect(); const s = getComputedStyle(e); return b.width > 0 && b.height > 0 && s.visibility !== "hidden"; };
          for (const e of document.querySelectorAll("button, summary, a.quiet, a.brand, .lead-row, .track .links a, [role=slider], .drop")) {
            if (!vis(e) || e.closest("[hidden]")) continue;
            const b = e.getBoundingClientRect();
            if (e.getAttribute("role") === "slider" && e.tagName === "CANVAS") continue;      // the waveform is 72 px or more high and the whole width
            if (Math.min(b.width, b.height) < 43.5) out.small.push(`${e.tagName.toLowerCase()}.${e.className} "${(e.getAttribute("aria-label") || e.textContent).trim().slice(0, 24)}" ${Math.round(b.width)}x${Math.round(b.height)}`);
          }
          for (const e of document.querySelectorAll("input:not([type=file]):not([hidden]), textarea, select")) if (vis(e) && parseFloat(getComputedStyle(e).fontSize) < 16) out.smallText.push(e.tagName + " " + getComputedStyle(e).fontSize);
          return out;
        });
        const tabsCut = await p.evaluate(() => { const n = document.getElementById("tabs"); return n && n.scrollWidth > n.clientWidth + 1 ? n.scrollWidth - n.clientWidth : 0; });
        note(name, `${label}: the Studio tabs are cut off by ${tabsCut}px`, tabsCut > 0);
        note(name, `${label}: page is ${r.overflow}px wider than the screen`, r.overflow > 0);
        note(name, `${label}: targets under 44 px: ${r.small.join("; ")}`, r.small.length > 0);
        note(name, `${label}: inputs under 16 px (iOS would zoom): ${r.smallText.join("; ")}`, r.smallText.length > 0);
        note(name, `${label}: viewport lacks viewport-fit=cover`, !/viewport-fit=cover/.test(r.vp));
      };
      const shot = async (p, label) => { if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `${name.replace(/ /g, "-")}-${label}.png`) }); };

      // ---- the public list ----
      let pub = await open(B, false);
      await pub.waitForSelector("article.item canvas.wave");
      await audit(pub, "public list"); await shot(pub, "list");
      // vertical swipe that starts ON a waveform must scroll the page and must not start playback
      const cdp = await pub.context().newCDPSession(pub);
      const wb = await pub.locator("canvas.wave").nth(1).boundingBox();
      const canScroll = await pub.evaluate(() => document.documentElement.scrollHeight > window.innerHeight + 50);
      // The browser, not this page, decides whether a vertical drag scrolls (it sends pointercancel); an emulated swipe cannot show that, so check the two things that make it work:
      note(name, "the waveform does not leave vertical drags to the browser (touch-action is not pan-y)", await pub.evaluate(() => getComputedStyle(document.querySelector("canvas.wave")).touchAction) !== "pan-y");
      // ...and that a drag which moved a long way is not taken for a tap, so it never starts playback
      if (wb && wb.y + wb.height < (desc.viewport.height - 10)) {
        await cdp.send("Input.synthesizeScrollGesture", { x: wb.x + wb.width / 2, y: wb.y + wb.height / 2, yDistance: -200, gestureSourceType: "touch", speed: 800 });
        await pub.waitForTimeout(250);
        note(name, "a vertical swipe starting on a waveform started playback", await pub.evaluate(() => !document.getElementById("bar").hidden));
      }
      await pub.evaluate(() => window.scrollTo(0, 0));
      // tap the play button of the second track
      await pub.locator("article.item .play").nth(1).tap();
      await pub.waitForFunction(() => !document.getElementById("bar").hidden, null, { timeout: 5000 });
      await pub.waitForFunction(() => document.querySelectorAll("article.item")[1].classList.contains("playing"));
      const want = await pub.evaluate(() => document.querySelectorAll("article.item .title")[1].textContent);
      const meta = await pub.evaluate(() => navigator.mediaSession && navigator.mediaSession.metadata && navigator.mediaSession.metadata.title);
      note(name, `the lock screen shows "${meta}", not "${want}"`, meta !== want);
      // the bar must not cover the last track once we scroll to the end
      await pub.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await pub.waitForTimeout(150);
      const cover = await pub.evaluate(() => { const bar = document.getElementById("bar").getBoundingClientRect(); const last = [...document.querySelectorAll("article.item")].pop().getBoundingClientRect(); return last.bottom - bar.top; });
      note(name, `the bottom bar covers the last track by ${Math.round(cover)} px`, cover > 0);
      await audit(pub, "public list with the bar"); await shot(pub, "list-playing");
      await pub.context().close();

      // ---- a single track (what a friend opens) ----
      pub = await open(trackUrl, false);
      await pub.waitForSelector("canvas.wave");
      await audit(pub, "track page"); await shot(pub, "track");
      const cv = await pub.locator("canvas.wave").boundingBox();
      await pub.touchscreen.tap(cv.x + cv.width * 0.5, cv.y + cv.height / 2);       // a tap on the waveform seeks and plays
      await pub.waitForFunction(() => !document.getElementById("bar").hidden, null, { timeout: 5000 });
      await pub.waitForFunction(() => Number(document.querySelector("canvas.wave").getAttribute("aria-valuenow")) > 0, null, { timeout: 6000 }).catch(() => {});
      const at = await pub.evaluate(() => Number(document.querySelector("canvas.wave").getAttribute("aria-valuenow")));
      note(name, `a tap in the middle of the waveform landed at ${at}s of 20`, !(at >= 7 && at <= 13));
      // a horizontal drag scrubs
      const cdp2 = await pub.context().newCDPSession(pub);
      const y = cv.y + cv.height / 2, x0 = cv.x + cv.width * 0.2, x1 = cv.x + cv.width * 0.8;
      await cdp2.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x0, y }] });
      for (let i = 1; i <= 6; i++) await cdp2.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x0 + ((x1 - x0) * i) / 6, y }] });
      await cdp2.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await pub.waitForTimeout(200);
      const at2 = await pub.evaluate(() => Number(document.querySelector("canvas.wave").getAttribute("aria-valuenow")));
      note(name, `a horizontal drag from 20% to 80% ended at ${at2}s of 20`, !(at2 >= 13));
      await shot(pub, "track-playing");
      await pub.context().close();

      // ---- the Studio ----
      const st = await open(B + "studio", true);
      await st.waitForSelector("article.item canvas.wave");
      await audit(st, "studio"); await shot(st, "studio");
      await st.locator("article.item").first().getByRole("button", { name: "Edit" }).click();
      await st.waitForSelector("article.item input[type=text]");
      await audit(st, "studio edit form"); await shot(st, "studio-edit");
      await st.getByRole("button", { name: "Backup" }).click();
      await st.waitForSelector("text=Speed check");
      await audit(st, "studio backup tab");
      await ctx.close();
      note(name, "console or page errors: " + errors.join(" | "), errors.length > 0);
      console.log((problems.filter((p) => p.startsWith(name + ":")).length ? "FAIL " : "ok   ") + name);
    }
    if (problems.length) { console.error("PROBLEMS:\n- " + problems.join("\n- ")); process.exitCode = 1; } else console.log("ALL PASSED");
  } catch (e) { console.error("FAILED:", e.stack || e.message); process.exitCode = 1; }
  finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
