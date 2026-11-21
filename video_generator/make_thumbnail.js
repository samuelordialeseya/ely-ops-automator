const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/laptop ni sam/.gemini/config/skills/playwright/node_modules/playwright');

(async () => {
  const kfPath = path.join(__dirname, 'build', 'keyframe_scene_03.png');
  const outPath = path.resolve(__dirname, '..', 'docs', 'demo_thumbnail.png');
  const imgB64 = fs.readFileSync(kfPath).toString('base64');

  const html = `<!DOCTYPE html>
<html>
<body style="margin:0;width:1920px;height:1080px;overflow:hidden;position:relative;background:#0A0F1C;">
  <img src="data:image/png;base64,${imgB64}" style="width:1920px;height:1080px;display:block;filter:brightness(0.84);" />
  <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:radial-gradient(circle,rgba(10,15,28,0.48) 0%,rgba(10,15,28,0.16) 65%);">
    <div style="width:128px;height:128px;border-radius:50%;background:rgba(16,185,129,0.94);display:flex;align-items:center;justify-content:center;box-shadow:0 20px 60px rgba(0,0,0,0.6),0 0 0 12px rgba(16,185,129,0.22);">
      <svg width="52" height="52" viewBox="0 0 24 24" fill="#0A0F1C" style="margin-left:6px;"><polygon points="5 3 19 12 5 21 5 3"/></svg>
    </div>
    <div style="margin-top:22px;padding:10px 24px;border-radius:8px;background:rgba(10,15,28,0.88);border:1px solid rgba(148,163,184,0.35);color:#F8FAFC;font-family:'Segoe UI',sans-serif;font-size:22px;font-weight:700;letter-spacing:0.01em;">
      Watch 1080p Narrated Walkthrough (1:50)
    </div>
  </div>
</body>
</html>`;

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.setContent(html);
  await page.screenshot({ path: outPath, type: 'png' });
  await browser.close();
  console.log('Saved thumbnail:', outPath);
})();
