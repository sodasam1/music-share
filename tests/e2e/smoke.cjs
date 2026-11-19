// Browser smoke test: drives the real studio and public pages in Chromium (needs node + playwright, ffmpeg, python with Flask).
//   MS_PY=/path/to/python node tests/e2e/smoke.cjs
// Fails on any console error or page error, which includes Content-Security-Policy violations.
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), assert = require("assert");
const { chromium } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8191;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-e2e-"));
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
for (const [i, hz] of [[1, 330], [2, 440]]) execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", `sine=frequency=${hz}:duration=3`, path.join(tmp, `Tone ${i}.wav`)]);
const server = spawn(PY, ["-m", "waitress", `--listen=127.0.0.1:${PORT}`, "wsgi:app"], { cwd: path.join(ROOT, "app"), stdio: "inherit", env: { ...process.env,
  MUSICSHARE_DATA_DIR: data, MUSICSHARE_BASE_PATH: "/music-share", MUSICSHARE_STATIC_DIR: path.join(ROOT, "static"), MUSICSHARE_SITE_KEY_FILE: path.join(auth, "keys", "musicshare.key"),
  MUSICSHARE_INSECURE_COOKIES: "1", MUSICSHARE_CONFIG: "/nonexistent" } });
const B = `http://127.0.0.1:${PORT}/music-share/`;
const step = (m) => console.log("ok  " + m);
(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(B + "health")).ok) break; } catch (e) {} await new Promise((r) => setTimeout(r, 100)); }
  const browser = await chromium.launch();
  const errors = [];
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
    await ctx.addCookies([{ name: "site_musicshare", value: cookie, url: B }]);
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
    page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
    await page.goto(B + "studio");
    await page.locator('input[type=file]').setInputFiles([path.join(tmp, "Tone 1.wav"), path.join(tmp, "Tone 2.wav")]);
    await page.waitForSelector("article.item canvas.wave");
    await page.waitForFunction(() => document.querySelectorAll("article.item").length === 2);
    step("two files uploaded and listed with waveforms");
    await page.waitForFunction(() => !document.body.textContent.includes("measuring waveform"), null, { timeout: 15000 });
    step("waveforms were measured in the background");
    const first = page.locator("article.item").first();
    await first.getByRole("button", { name: "Edit" }).click();
    await first.locator("select").selectOption("listed");
    await first.locator("input[type=text]").fill("A <b>bold</b> title");
    await first.getByRole("button", { name: "Save" }).click();
    await page.waitForFunction(() => document.body.textContent.includes("A <b>bold</b> title"));
    assert.strictEqual(await page.locator("article.item b").count(), 0);
    step("editing works and a title with HTML stays text");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 0, "horizontal overflow on a phone: " + overflow);
    step("phone width: no sideways scroll");
    await page.getByRole("button", { name: "Playlists" }).click();
    page.once("dialog", (d) => d.accept("For Dana"));
    await page.getByRole("button", { name: "New playlist" }).click();
    await page.waitForSelector("article.item select");
    await page.locator("article.item select").selectOption({ index: 1 });
    await page.getByRole("button", { name: "Save order" }).click();
    step("a playlist can be made and filled");
    const pub = await browser.newPage();            // a stranger: no cookie
    pub.on("console", (m) => { if (m.type() === "error") errors.push("public console: " + m.text()); });
    pub.on("pageerror", (e) => errors.push("public pageerror: " + e.message));
    await pub.goto(B);
    await pub.waitForSelector("article.item");
    assert.strictEqual(await pub.locator("article.item").count(), 1);
    assert.ok((await pub.textContent("body")).includes("A <b>bold</b> title"));
    step("the public page lists only the listed track");
    await pub.locator("article.item .play").click();
    await pub.waitForFunction(() => document.querySelector("article.item.playing") !== null);
    step("play starts from the public page");
    const r = await pub.request.get(B + "studio", { maxRedirects: 0 }).catch(() => null);
    const studio = await fetch(B + "api/tracks");
    assert.strictEqual(studio.status, 401);
    step("a stranger cannot reach the owner API");
    assert.deepStrictEqual(errors, [], "console/page errors:\n" + errors.join("\n"));
    step("no console or page errors (so no Content-Security-Policy violations)");
    console.log("ALL PASSED");
  } catch (e) { console.error("FAILED:", e.message, "\n", errors.join("\n")); process.exitCode = 1; }
  finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
