// Browser test for: lazy waveforms on the public page, the lossy/lossless switch, choosing several tracks in the Studio, replacing audio.
//   MS_PY=/path/to/python node tests/e2e/features.cjs
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), assert = require("assert");
const { chromium } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8197;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-feat-"));
const auth = path.join(tmp, "site-auth"), data = path.join(tmp, "data");
const keys = path.join(ROOT, "app", "siteauth", "keys.py");
execFileSync(PY, [keys, "init", "--dir", auth, "--no-chown"]);
execFileSync(PY, [keys, "ensure-app", "--dir", auth, "--app", "musicshare", "--path", "/music-share", "--no-chown"]);
const cookie = execFileSync(PY, ["-c", `
import importlib.util as u, time, sys
sp = u.spec_from_file_location("k", "${keys}"); k = u.module_from_spec(sp); sp.loader.exec_module(k)
sys.path.insert(0, "${path.join(ROOT, "app")}")
from siteauth import make_token
print(make_token(k.derive(open("${auth}/master").read().strip(), "musicshare", 1), time.time()))`]).toString().trim();
const N = 30;
for (let i = 0; i < N; i++) execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", `sine=frequency=${200 + i * 15}:duration=6`, "-ar", "44100", path.join(tmp, `t${i}.wav`)]);
execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=8", "-ar", "44100", path.join(tmp, "replacement.wav")]);
const server = spawn(PY, ["-m", "waitress", `--listen=127.0.0.1:${PORT}`, "wsgi:app"], { cwd: path.join(ROOT, "app"), stdio: "inherit", env: { ...process.env,
  MUSICSHARE_DATA_DIR: data, MUSICSHARE_BASE_PATH: "/music-share", MUSICSHARE_STATIC_DIR: path.join(ROOT, "static"), MUSICSHARE_SITE_KEY_FILE: path.join(auth, "keys", "musicshare.key"),
  MUSICSHARE_INSECURE_COOKIES: "1", MUSICSHARE_CONFIG: "/nonexistent" } });
const B = `http://127.0.0.1:${PORT}/music-share/`;
const H = { Cookie: "site_musicshare=" + cookie };
const step = (m) => console.log("ok  " + m);
(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(B + "health")).ok) break; } catch (e) {} await new Promise((r) => setTimeout(r, 100)); }
  for (let i = 0; i < N; i++) {
    const fd = new FormData(); fd.append("files", new Blob([fs.readFileSync(path.join(tmp, `t${i}.wav`))]), `Track ${String(i).padStart(2, "0")}.wav`); fd.append("visibility", "listed");
    assert.strictEqual((await fetch(B + "api/tracks", { method: "POST", body: fd, headers: H })).status, 201);
  }
  for (let i = 0; i < 300; i++) {
    const rows = await (await fetch(B + "api/tracks", { headers: H })).json();
    if (rows.every((t) => t.analysis_status !== "pending" && t.stream_status !== "pending")) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  const rows = await (await fetch(B + "api/tracks", { headers: H })).json();
  assert.ok(rows.every((t) => t.stream_status === "ready"), "every track has its streaming copy");
  step(`${N} tracks uploaded; each has a waveform and a streaming copy`);
  const browser = await chromium.launch();
  const errors = [];
  try {
    // ---- public page -----------------------------------------------------------------------------------------------
    const ctx = await browser.newContext({ viewport: { width: 900, height: 700 } });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    const audioReqs = [];
    page.on("response", (r) => { if (/\/a\/[^/]+(\?|$)/.test(r.url())) audioReqs.push({ url: r.url(), type: r.headers()["content-type"] }); });
    await page.goto(B); await page.waitForSelector("article.item canvas.wave");
    await page.waitForTimeout(500);
    const drawn = await page.evaluate(() => [...document.querySelectorAll("article.item")].filter((i) => i._wave).length);
    assert.ok(drawn > 0 && drawn < N, `only the rows near the screen draw their waveform at first (${drawn} of ${N})`);
    step(`public page draws ${drawn} of ${N} waveforms at first`);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await page.waitForTimeout(600);
    assert.ok(await page.evaluate(() => [...document.querySelectorAll("article.item")].pop()._wave), "the last row draws when scrolled into view");
    step("a row far down draws its waveform once scrolled near");
    await page.evaluate(() => window.scrollTo(0, 0));
    // lossy by default
    assert.strictEqual((await page.textContent("#quality")).trim(), "Playback: lossy");
    await page.locator("article.item .play").first().click();
    await page.waitForFunction(() => !document.getElementById("bar").hidden);
    await page.waitForTimeout(500);
    assert.ok(audioReqs.length && audioReqs.every((r) => r.type === "audio/mp4" && !/q=lossless/.test(r.url)), "default playback fetches the small AAC copy: " + JSON.stringify(audioReqs));
    step("default playback streams the small copy (audio/mp4)");
    // switch to lossless while playing
    await page.waitForFunction(() => document.querySelector("article.item .t-now").textContent !== "0:00", null, { timeout: 8000 });
    audioReqs.length = 0;
    await page.click("#quality");
    assert.strictEqual((await page.textContent("#quality")).trim(), "Playback: lossless");
    await page.waitForFunction(() => document.querySelector("article.item.playing .play").dataset.state === "pause", null, { timeout: 8000 });
    await page.waitForTimeout(500);
    assert.ok(audioReqs.some((r) => /q=lossless/.test(r.url) && r.type === "audio/wav"), "lossless fetches the original: " + JSON.stringify(audioReqs));
    const resumed = await page.evaluate(() => document.querySelector("article.item .t-now").textContent);
    assert.notStrictEqual(resumed, "0:00", "switching carries on from the same second");
    step("switching to lossless while playing fetches the original and carries on from the same second");
    await page.reload(); await page.waitForSelector("#quality:not([hidden])");
    assert.strictEqual((await page.textContent("#quality")).trim(), "Playback: lossless");
    step("the choice is remembered after a reload");
    await ctx.close();

    // ---- Studio ----------------------------------------------------------------------------------------------------
    const sctx = await browser.newContext({ viewport: { width: 900, height: 900 } });
    await sctx.addCookies([{ name: "site_musicshare", value: cookie, url: B }]);
    const st = await sctx.newPage();
    st.on("console", (m) => { if (m.type() === "error") errors.push("studio console: " + m.text()); });
    st.on("pageerror", (e) => errors.push("studio pageerror: " + e.message));
    await st.goto(B + "studio"); await st.waitForSelector("article.item");
    assert.strictEqual((await st.textContent("#quality")).trim(), "Playback: lossy", "the Studio starts lossy too (a different browser profile)");
    assert.ok((await st.locator("article.item .track-meta").first().textContent()).includes("friends stream a"), "the row says what friends stream");
    step("Studio shows the switch (lossy by default) and what friends stream");
    // choose several
    await st.getByRole("button", { name: "Select…" }).click();
    await st.waitForSelector("input.pick");
    const picks = st.locator("input.pick");
    await picks.nth(0).check(); await picks.nth(1).check();
    assert.ok((await st.textContent("#pick-count")).includes("2 selected"));
    await st.getByRole("button", { name: "Make unlisted" }).click();
    await st.waitForFunction(() => document.getElementById("notice").textContent.includes("2 track(s) are now unlisted"));
    const listedNow = (await (await fetch(B + "api/tracks", { headers: H })).json()).filter((t) => t.visibility === "unlisted").length;
    assert.strictEqual(listedNow, 2);
    step("two tracks chosen and made unlisted with one click");
    await st.getByRole("button", { name: "All" }).click();
    assert.ok((await st.textContent("#pick-count")).includes(N + " selected"));
    await st.getByRole("button", { name: "None" }).click();
    await st.getByRole("button", { name: "Done" }).click();
    assert.strictEqual(await st.locator("input.pick").count(), 0);
    step("All, None and Done work");
    // replace audio
    const first = st.locator("article.item").first();
    const before = await (await fetch(B + "api/tracks", { headers: H })).json();
    const fc = st.waitForEvent("filechooser");
    st.once("dialog", (d) => d.accept());
    await first.getByRole("button", { name: "Replace audio…" }).click();
    await (await fc).setFiles(path.join(tmp, "replacement.wav"));
    await st.waitForFunction(() => document.getElementById("notice").textContent.includes("Replaced"), null, { timeout: 8000 });
    const after = await (await fetch(B + "api/tracks", { headers: H })).json();
    const was = before[0], now = after.find((t) => t.id === was.id);
    assert.strictEqual(now.token, was.token); assert.strictEqual(now.title, was.title); assert.notStrictEqual(now.size, was.size);
    step("replacing the audio keeps the link and title and changes the file");
    assert.deepStrictEqual(errors, []);
    console.log("ALL PASSED");
  } catch (e) { console.error("FAILED:", e.stack || e.message); process.exitCode = 1; }
  finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
