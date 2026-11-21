/**
 * Deterministic Playwright + FFmpeg 1080p Video Renderer for ely-ops-automator.
 *
 * - Uses Playwright with local Google Chrome (`channel: 'chrome'`) and `deviceScaleFactor: 2` (2x Retina supersampling).
 * - Steps frame-by-frame at 30 FPS through `video_generator/build/timeline.json`.
 * - Pipes supersampled frames into FFmpeg (`scale=1920:1080:flags=lanczos`, `libx264`, `yuv420p`)
 *   and multiplexes `narration_full.wav` into `output/ely_ops_demo_1080p.mp4`.
 * - Saves 6 Retina PNG keyframes (`video_generator/build/keyframe_scene_01.png` .. `06.png`) for visual verification.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');

const PLAYWRIGHT_PATH = 'C:/Users/laptop ni sam/.gemini/config/skills/playwright/node_modules/playwright';
const { chromium } = require(PLAYWRIGHT_PATH);

const ROOT_DIR = path.resolve(__dirname, '..');
const VIDEO_DIR = __dirname;
const BUILD_DIR = path.join(VIDEO_DIR, 'build');
const OUTPUT_DIR = path.join(ROOT_DIR, 'output');

const TIMELINE_PATH = path.join(BUILD_DIR, 'timeline.json');
const STAGE_HTML_PATH = path.join(VIDEO_DIR, 'stage.html');
const OUT_MP4_PATH = path.join(OUTPUT_DIR, 'ely_ops_demo_1080p.mp4');

function findActiveCaption(scene, localSec) {
  for (const p of scene.phrases) {
    if (localSec >= p.local_start_sec && localSec <= p.local_end_sec) {
      return p.text;
    }
  }
  if (localSec < scene.phrases[0].local_start_sec) {
    return scene.phrases[0].text;
  }
  return scene.phrases[scene.phrases.length - 1].text;
}

function writeBuffer(stream, buf) {
  return new Promise((resolve, reject) => {
    const ok = stream.write(buf, (err) => {
      if (err) reject(err);
    });
    if (ok) {
      resolve();
    } else {
      stream.once('drain', resolve);
    }
  });
}

(async () => {
  if (!fs.existsSync(TIMELINE_PATH)) {
    throw new Error(`Missing timeline.json at ${TIMELINE_PATH}. Run generate_voiceover.py first.`);
  }

  const timeline = JSON.parse(fs.readFileSync(TIMELINE_PATH, 'utf-8'));
  const fps = timeline.fps || 30;
  const totalDurationSec = timeline.total_duration_sec;
  const totalFrames = timeline.total_frames;

  console.log(`[renderer] Launching Chrome (2x Retina supersampling) for ${totalFrames} frames (${totalDurationSec}s @ ${fps}fps)...`);

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
  });

  const page = await context.newPage();
  const stageUrl = pathToFileURL(STAGE_HTML_PATH).href;
  await page.goto(stageUrl, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);

  const ffmpegArgs = [
    '-y',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-framerate', String(fps),
    '-i', '-',
    '-i', timeline.master_audio_path,
    '-vf', 'scale=1920:1080:flags=lanczos',
    '-c:v', 'libx264',
    '-preset', 'medium',
    '-crf', '17',
    '-pix_fmt', 'yuv420p',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-movflags', '+faststart',
    '-shortest',
    OUT_MP4_PATH,
  ];

  const ffmpegProc = spawn('ffmpeg', ffmpegArgs, {
    stdio: ['pipe', 'ignore', 'pipe'],
  });

  let ffmpegStderr = '';
  ffmpegProc.stderr.on('data', (chunk) => {
    ffmpegStderr += chunk.toString();
  });

  const ffmpegDone = new Promise((resolve, reject) => {
    ffmpegProc.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`FFmpeg exited with code ${code}:\n${ffmpegStderr.slice(-1500)}`));
    });
  });

  let globalFrame = 0;

  for (const scene of timeline.scenes) {
    const sIdx = scene.index;
    const sFrames = scene.frame_count;
    const sDur = scene.duration_sec;
    const keyframeTarget = Math.min(sFrames - 1, Math.floor(fps * 3.8));

    let cachedJpeg = null;
    let lastStateKey = '';

    console.log(`[renderer] Rendering ${scene.id} (${sFrames} frames, ${sDur.toFixed(2)}s)...`);

    for (let f = 0; f < sFrames; f++) {
      const localSec = f / fps;
      const globalSec = globalFrame / fps;
      const captionText = findActiveCaption(scene, localSec);

      // Determine whether continuous entrance/scroll animation is active on this frame
      const isEntranceActive = localSec <= 3.8;
      const isScrollScene = sIdx === 4 && (f % 2 === 0); // smooth 15fps scroll sampling in scene 5
      const secBucket = Math.floor(globalSec);
      const progressBucket = Math.floor(globalFrame / 6); // update progress bar 5x/sec after entrance
      const highlightBucket = Math.floor((localSec / Math.max(1, sDur)) * 10);

      const stateKey = isEntranceActive || isScrollScene
        ? `frame_${globalFrame}`
        : `settled_${sIdx}_${secBucket}_${progressBucket}_${highlightBucket}_${captionText}`;

      if (!cachedJpeg || stateKey !== lastStateKey || f === keyframeTarget) {
        await page.evaluate((st) => {
          window.__setFrame(st);
        }, {
          sceneIndex: sIdx,
          chapter: scene.chapter,
          localSec,
          sceneDurationSec: sDur,
          globalSec,
          totalDurationSec,
          captionText,
        });

        cachedJpeg = await page.screenshot({
          type: 'jpeg',
          quality: 92,
        });
        lastStateKey = stateKey;

        if (f === keyframeTarget) {
          const kfPath = path.join(BUILD_DIR, `keyframe_${scene.id}.png`);
          await page.screenshot({
            path: kfPath,
            type: 'png',
          });
        }
      }

      await writeBuffer(ffmpegProc.stdin, cachedJpeg);
      globalFrame++;
    }
  }

  ffmpegProc.stdin.end();
  await ffmpegDone;
  await browser.close();

  const stat = fs.statSync(OUT_MP4_PATH);
  console.log(`[renderer] Success! Output video: ${OUT_MP4_PATH} (${(stat.size / (1024 * 1024)).toFixed(2)} MB)`);
})();
