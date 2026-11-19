// Proves the Studio shows that it heard a click while a slow save is in flight: the button says "Working...", the row dims, the top bar runs;
// and that the row is updated in place afterwards without a second round trip.   MS_PY=... node tests/e2e/busy.cjs
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), assert = require("assert");
const { chromium } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8194;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-busy-"));
const auth = path.join(tmp, "site-auth"), data = path.join(tmp, "data"), keys = path.join(ROOT, "app", "siteauth", "keys.py");
execFileSync(PY, [keys, "init", "--dir", auth, "--no-chown"]);
execFileSync(PY, [keys, "ensure-app", "--dir", auth, "--app", "musicshare", "--path", "/music-share", "--no-chown"]);
const cookie = execFileSync(PY, ["-c", `
import importlib.util as u, time, sys
sp = u.spec_from_file_location("k", "${keys}"); k = u.module_from_spec(sp); sp.loader.exec_module(k)
sys.path.insert(0, "${path.join(ROOT, "app")}")
from siteauth import make_token
print(make_token(k.derive(open("${auth}/master").read().strip(), "musicshare", 1), time.time()))`]).toString().trim();
execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", path.join(tmp, "t.wav")]);
const server = spawn(PY, ["-m", "waitress", `--listen=127.0.0.1:${PORT}`, "wsgi:app"], { cwd: path.join(ROOT, "app"), stdio: "ignore", env: { ...process.env,
  MUSICSHARE_DATA_DIR: data, MUSICSHARE_BASE_PATH: "/music-share", MUSICSHARE_STATIC_DIR: path.join(ROOT, "static"),
  MUSICSHARE_SITE_KEY_FILE: path.join(auth, "keys", "musicshare.key"), MUSICSHARE_INSECURE_COOKIES: "1", MUSICSHARE_CONFIG: "/nonexistent" } });
const B = `http://127.0.0.1:${PORT}/music-share/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(B + "health")).ok) break; } catch (e) {} await sleep(100); }
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
    await ctx.addCookies([{ name: "site_musicshare", value: cookie, url: B }]);
    const page = await ctx.newPage(); const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); }); page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(B + "studio");
    await page.locator("input[type=file]").setInputFiles(path.join(tmp, "t.wav"));
    await page.waitForSelector("article.item");
    let patches = 0, lists = 0;
    await page.route("**/api/tracks/*", async (route) => { if (route.request().method() === "PATCH") { patches++; await sleep(2500); } await route.continue(); });
    await page.route("**/api/tracks", async (route) => { if (route.request().method() === "GET") lists++; await route.continue(); });
    await page.getByRole("button", { name: "Edit" }).click();
    await page.locator("select[aria-label=Visibility]").selectOption("listed");
    const t0 = Date.now();
    await page.getByRole("button", { name: "Save" }).click();
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => b.textContent === "Working…"), null, { timeout: 1000 });
    const heard = Date.now() - t0;
    assert.ok(heard < 500, "the click was not acknowledged within half a second: " + heard);
    console.log(`ok  the button says "Working…" ${heard} ms after the click, while the save takes 2.5 s`);
    assert.ok(await page.locator("article.item.pending").count() === 1);
    console.log("ok  the row is dimmed while it saves");
    await page.waitForSelector("#net:not([hidden])", { timeout: 1500 });
    console.log("ok  the bar at the top of the page is running");
    const listsBefore = lists;
    await page.waitForFunction(() => document.body.textContent.includes("listed") && !document.querySelector("article.item.pending"), null, { timeout: 8000 });
    assert.strictEqual(patches, 1);
    assert.strictEqual(lists, listsBefore, "the list was fetched again after the save");
    console.log("ok  the row updates in place afterwards, with no second request to reload the list");
    await page.waitForSelector("#net", { state: "hidden" });
    console.log("ok  the bar goes away when the server has answered");
    assert.deepStrictEqual(errors, []);
    console.log("ALL PASSED");
  } catch (e) { console.error("FAILED:", e.message); process.exitCode = 1; }
  finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
