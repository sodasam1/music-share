// Measures how quickly the Studio shows its menu and its first rows when the server is slow to answer.
//   MS_PY=/path/to/python [API_DELAY_MS=300] [TRACKS=40] node tests/e2e/first-paint.cjs
// It makes TRACKS tracks, then loads the Studio with every /api/ answer delayed by API_DELAY_MS (a stand-in for a slow disk or a slow link)
// and prints the time until (1) the tab menu is visible, (2) the first row or a placeholder for it is visible, (3) real rows are shown.
// The numbers are for comparing before and after a change on this machine, not a promise about your server.
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const { chromium } = require("playwright");
const PY = process.env.MS_PY || "python3", ROOT = path.resolve(__dirname, "..", ".."), PORT = 8193;
const DELAY = Number(process.env.API_DELAY_MS || 300), N = Number(process.env.TRACKS || 40);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ms-fp-"));
const auth = path.join(tmp, "site-auth"), data = path.join(tmp, "data"), keys = path.join(ROOT, "app", "siteauth", "keys.py");
execFileSync(PY, [keys, "init", "--dir", auth, "--no-chown"]);
execFileSync(PY, [keys, "ensure-app", "--dir", auth, "--app", "musicshare", "--path", "/music-share", "--no-chown"]);
const cookie = execFileSync(PY, ["-c", `
import importlib.util as u, time, sys
sp = u.spec_from_file_location("k", "${keys}"); k = u.module_from_spec(sp); sp.loader.exec_module(k)
sys.path.insert(0, "${path.join(ROOT, "app")}")
from siteauth import make_token
print(make_token(k.derive(open("${auth}/master").read().strip(), "musicshare", 1), time.time()))`]).toString().trim();
execFileSync("ffmpeg", ["-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=4", path.join(tmp, "t.wav")]);
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
    const seed = await ctx.newPage(); await seed.goto(B + "studio");
    const bytes = fs.readFileSync(path.join(tmp, "t.wav"));
    for (let i = 0; i < N; i++) {
      const r = await seed.evaluate(async ([b64, n]) => { const bin = atob(b64), a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
        const fd = new FormData(); fd.append("files", new File([a], "Track " + n + ".wav")); const r = await fetch("api/tracks", { method: "POST", body: fd, credentials: "same-origin" }); return r.status; }, [bytes.toString("base64"), i]);
      if (r !== 201) throw new Error("seed upload failed " + r);
    }
    await sleep(2500); await seed.close();            // let the background analysis finish so rows have waveforms
    const runs = [];
    for (let k = 0; k < 3; k++) {
      const page = await ctx.newPage();
      let requests = 0;
      await page.route("**/api/**", async (route) => { requests++; await sleep(DELAY); await route.continue(); });
      const t0 = Date.now(); const mark = {};
      page.goto(B + "studio");
      await page.waitForSelector("#tabs button", { timeout: 20000 }); mark.menu = Date.now() - t0;
      await page.waitForSelector("#main .item, #main .skeleton", { timeout: 20000 }); mark.first = Date.now() - t0;
      await page.waitForSelector("#main article.item canvas.wave", { timeout: 20000 }); mark.rows = Date.now() - t0;
      await sleep(300); mark.requests = requests;
      runs.push(mark); await page.close();
    }
    const med = (key) => runs.map((r) => r[key]).sort((a, b) => a - b)[1];
    console.log(`API delay ${DELAY} ms, ${N} tracks, median of 3 loads:`);
    console.log(`  menu visible        ${med("menu")} ms`);
    console.log(`  first row/skeleton  ${med("first")} ms`);
    console.log(`  real rows shown     ${med("rows")} ms`);
    console.log(`  API requests made   ${med("requests")}`);
  } finally { await browser.close(); server.kill(); fs.rmSync(tmp, { recursive: true, force: true }); }
})();
