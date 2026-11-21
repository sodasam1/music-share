// The live server's relayd adds its own Content-Security-Policy (default-src https: 'unsafe-inline') to every answer, on top of the app's own.
// Browsers enforce both, so anything the app loads from a data: URL (icons drawn with CSS masks, the logo) is blocked there but not on a plain test server.
// This test sends every page through the same extra headers and checks that the play/pause symbols and the logo still draw.
//   MS_PY=/path/to/python node tests/e2e/relayd-headers.cjs      (needs Pillow in that Python)
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), assert = require("assert");
const { chromium } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8196;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-csp-"));
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
execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=5", path.join(tmp, "a.wav")]);
const server = spawn(PY, ["-m", "waitress", `--listen=127.0.0.1:${PORT}`, "wsgi:app"], { cwd: path.join(ROOT, "app"), stdio: "inherit", env: { ...process.env,
  MUSICSHARE_DATA_DIR: data, MUSICSHARE_BASE_PATH: "/music-share", MUSICSHARE_STATIC_DIR: path.join(ROOT, "static"), MUSICSHARE_SITE_KEY_FILE: path.join(auth, "keys", "musicshare.key"),
  MUSICSHARE_INSECURE_COOKIES: "1", MUSICSHARE_CONFIG: "/nonexistent" } });
const B = `http://127.0.0.1:${PORT}/music-share/`;
const RELAYD = { "Content-Security-Policy": `default-src https: http://127.0.0.1:${PORT} 'unsafe-inline'`, "X-Frame-Options": "SAMEORIGIN", "Referrer-Policy": "strict-origin" };   // from the live relayd.conf; the test server is plain http on loopback, so its own origin is added to the policy (data: stays blocked, as live)
// share of pixels in the middle of an element that differ from the element's own fill colour (the symbol is drawn in another colour)
function inkShare(png, box) {
  const f = path.join(tmp, "s.png"); fs.writeFileSync(f, png);
  return Number(execFileSync(PY, ["-I", "-c", `
from PIL import Image
im = Image.open("${f}").convert("RGB"); w, h = im.size
x0, y0, x1, y1 = ${box.x0}, ${box.y0}, ${box.x1}, ${box.y1}
bg = im.getpixel((x0 + 1, (y0 + y1) // 2 if False else y0 + 1))
n = d = 0
for x in range(x0, x1):
    for y in range(y0, y1):
        n += 1; p = im.getpixel((x, y)); d += sum(abs(a - b) for a, b in zip(p, bg)) > 120
print(d / n)`]).toString());
}
(async () => {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(B + "health")).ok) break; } catch (e) {} await new Promise((r) => setTimeout(r, 100)); }
  const fd = new FormData(); fd.append("files", new Blob([fs.readFileSync(path.join(tmp, "a.wav"))]), "a.wav"); fd.append("visibility", "listed");
  assert.strictEqual((await fetch(B + "api/tracks", { method: "POST", body: fd, headers: { Cookie: "site_musicshare=" + cookie } })).status, 201);
  await new Promise((r) => setTimeout(r, 2500));
  const browser = await chromium.launch(); let bad = [];
  try {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 420, height: 700 }, deviceScaleFactor: 2, colorScheme: scheme });
      await ctx.route("**/*", async (route) => { const r = await route.fetch(); await route.fulfill({ response: r, headers: { ...r.headers(), ...RELAYD } }); });
      const csp = [];
      const page = await ctx.newPage();
      page.on("console", (m) => { if (/Content Security Policy/i.test(m.text())) csp.push(m.text().slice(0, 140)); });
      await page.goto(B); await page.waitForSelector("article.item .play");
      const btn = page.locator("article.item .play").first();
      const b = await btn.boundingBox(), s = 2;
      const box = { x0: Math.round((b.x + b.width * 0.3) * s), y0: Math.round((b.y + b.height * 0.3) * s), x1: Math.round((b.x + b.width * 0.7) * s), y1: Math.round((b.y + b.height * 0.7) * s) };
      const play = inkShare(await page.screenshot(), box);
      if (play < 0.12) bad.push(`${scheme}: the play button shows no symbol (${(play * 100).toFixed(1)}% of its middle differs from its fill)`);
      await btn.click(); await page.waitForFunction(() => document.querySelector("article.item .play").dataset.state === "pause", null, { timeout: 5000 });
      await page.mouse.move(0, 0);
      const pause = inkShare(await page.screenshot(), box);
      if (process.env.MS_SHOTS) { fs.mkdirSync(process.env.MS_SHOTS, { recursive: true }); await page.screenshot({ path: path.join(process.env.MS_SHOTS, `csp-${scheme}-public-playing.png`) }); }
      if (pause < 0.12) bad.push(`${scheme}: the pause button shows no symbol (${(pause * 100).toFixed(1)}%)`);
      const logo = await page.locator(".brand svg, .brand .logo").count();
      if (!logo) bad.push(`${scheme}: the header has no logo drawn from inline markup (a data: URL logo is blocked on the live server)`);
      if (csp.length) bad.push(`${scheme}: the browser blocked something: ${csp[0]}`);
      console.log(`${bad.length ? "FAIL" : "ok  "} ${scheme}: play ${(play * 100).toFixed(0)}%, pause ${(pause * 100).toFixed(0)}% of the middle is symbol`);
      await ctx.close();
    }
    if (bad.length) { console.error("PROBLEMS:\n- " + bad.join("\n- ")); process.exitCode = 1; } else console.log("ALL PASSED");
  } catch (e) { console.error("FAILED:", e.stack || e.message); process.exitCode = 1; }
  finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
