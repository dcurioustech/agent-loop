const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  fs.rmSync('frames', { recursive: true, force: true }); fs.mkdirSync('frames');
  const b = await chromium.launch({ executablePath: 'process.env.CHROME_PATH || undefined' });
  const p = await b.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  await p.goto('file://' + __dirname + '/agent-loop-summary.html');
  await p.waitForTimeout(500);
  const total = await p.evaluate(() => { window.start(); return window.TOTAL_MS; });
  const t0 = Date.now(); const list = []; let i = 0; let prev = null;
  while (Date.now() - t0 < total + 300) {
    const t = Date.now() - t0;
    const f = `frames/f${String(i).padStart(5,'0')}.jpg`;
    await p.screenshot({ path: f, type: 'jpeg', quality: 95 });
    if (prev) list.push(`file '${prev.f}'\nduration ${((t - prev.t)/1000).toFixed(3)}`);
    prev = { f, t }; i++;
  }
  list.push(`file '${prev.f}'\nduration 0.5\nfile '${prev.f}'`);
  fs.writeFileSync('frames/list.txt', list.join('\n').replace(/frames\//g, ''));
  console.log('frames', i, 'fps', (i / (total/1000)).toFixed(1));
  await b.close();
})();
