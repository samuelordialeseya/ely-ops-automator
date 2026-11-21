"""
Voiceover & Timeline Generator for ely-ops-automator Product Demo Video.

Primary Engine : ElevenLabs REST API (`eleven_multilingual_v2`) when ELEVENLABS_API_KEY is set.
Fallback Engine: Microsoft Studio Neural TTS (`edge-tts`, `en-US-AndrewMultilingualNeural`) when keyless.

Outputs:
- video_generator/build/scene_01.mp3 .. scene_06.mp3 (raw TTS)
- video_generator/build/scene_01_padded.wav .. scene_06_padded.wav (with editorial breath padding)
- video_generator/build/narration_full.wav (normalized master track)
- video_generator/build/timeline.json (exact millisecond/frame timings + phrase-level captions)
- output/ely_ops_demo_1080p.srt (standard SubRip subtitle file)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
VIDEO_DIR = Path(__file__).resolve().parent
BUILD_DIR = VIDEO_DIR / "build"
OUTPUT_DIR = ROOT_DIR / "output"

FPS = 30
PRE_PAD_SEC = 0.35
POST_PAD_SEC = 0.55

SCENES: list[dict[str, Any]] = [
    {
        "id": "scene_01",
        "index": 0,
        "chapter": "01 / THE NIGHTLY CLOSEOUT BOTTLENECK",
        "title": "Why Nightly Store Closeout Needed Automation",
        "narration": (
            "Running a neighborhood produce counter is fast during the day, "
            "but closing out the books at night used to be manual and error-prone. "
            "Every evening meant separating subdivision deliveries from walk-ins, "
            "cleaning JavaScript centavo rounding errors, and guessing tomorrow's "
            "four A.M. wholesale produce order from memory."
        ),
        "phrases": [
            "Running a neighborhood produce counter is fast during the day,",
            "but closing out the books at night used to be manual and error-prone.",
            "Every evening meant separating subdivision deliveries from walk-ins,",
            "cleaning JavaScript centavo rounding errors, and guessing tomorrow's 4:00 AM wholesale order from memory.",
        ],
    },
    {
        "id": "scene_02",
        "index": 1,
        "chapter": "02 / READ-ONLY PRODUCTION ARCHITECTURE",
        "title": "Zero-Risk Cloud Firestore & Deterministic Pipeline",
        "narration": (
            "Ely Ops Automator replaces that nightly routine with a scheduled, strictly read-only pipeline. "
            "It connects to the live Cloud Firestore database with zero write, update, or delete operations, "
            "feeding raw POS orders into a deterministic Python accounting engine and a schema-validated Gemini briefing."
        ),
        "phrases": [
            "Ely Ops Automator replaces that nightly routine with a scheduled, strictly read-only pipeline.",
            "It connects to the live Cloud Firestore database with zero write, update, or delete operations,",
            "feeding raw POS orders into a deterministic Python accounting engine and a schema-validated Gemini briefing.",
        ],
    },
    {
        "id": "scene_03",
        "index": 2,
        "chapter": "03 / EXACT DECIMAL CENTAVO RECONCILIATION",
        "title": "Scrubbing Floating-Point Drift to Zero Variance",
        "narration": (
            "First, the accounting engine reconciles every single order line using exact base-ten Decimal math. "
            "On this twenty-two thousand six hundred seventy-two peso day across twenty-four orders, "
            "it automatically scrubs ten historical JavaScript floating-point artifacts down to an exact zero-centavo bookkeeper ledger."
        ),
        "phrases": [
            "First, the accounting engine reconciles every single order line using exact base-ten Decimal math.",
            "On this ₱22,672.11 day across 24 orders,",
            "it automatically scrubs 10 historical JavaScript floating-point artifacts down to an exact ₱0.00 variance ledger.",
        ],
    },
    {
        "id": "scene_04",
        "index": 3,
        "chapter": "04 / SUBDIVISION ZONES & ANOMALY AUDIT",
        "title": "Delivery Zone Telemetry & Pre-Close Exception Catching",
        "narration": (
            "Next, the audit engine maps sales across Nuvali subdivision delivery zones, "
            "flags unclosed pending tickets before the register closes, "
            "and catches catalog price variances like Ponkan and Australian Grapes—"
            "while computing trailing seven-day sales velocity per kilogram and tray."
        ),
        "phrases": [
            "Next, the audit engine maps sales across Nuvali subdivision delivery zones,",
            "flags unclosed Pending tickets before the register closes,",
            "and catches catalog price variances like Ponkan and Australian Grapes—",
            "while computing trailing 7-day sales velocity per kilogram and tray.",
        ],
    },
    {
        "id": "scene_05",
        "index": 4,
        "chapter": "05 / 4:00 AM WHOLESALE BUYING GUIDE",
        "title": "Pydantic-Validated Gemini Briefing with Deterministic Fallback",
        "narration": (
            "Those verified numbers feed a strict Pydantic schema where Gemini synthesizes the owner's four A.M. wholesale buying guide. "
            "It highlights fast-moving fruits like Davao Lacatan at nearly eight times normal pace, "
            "caps soft produce buffers to prevent spoilage, and falls back to a deterministic engine if the API is ever offline."
        ),
        "phrases": [
            "Those verified numbers feed a strict Pydantic schema where Gemini synthesizes the owner's 4:00 AM buying guide.",
            "It highlights fast-moving fruits like Davao Lacatan at 7.89x normal pace,",
            "caps soft produce buffers to prevent spoilage, and falls back to a deterministic engine if offline.",
        ],
    },
    {
        "id": "scene_06",
        "index": 5,
        "chapter": "06 / AUTOMATED 9:00 PM PHT DELIVERY",
        "title": "Zero-Cost GitHub Actions Schedule & 90-Day Audit Trail",
        "narration": (
            "Every night at nine P.M. Manila time, GitHub Actions runs the entire suite on the free tier, "
            "archives ninety-day CSV audit trails, and delivers the HTML briefing straight to the owner's Gmail inbox—"
            "turning forty-five minutes of nightly bookkeeping into zero manual work."
        ),
        "phrases": [
            "Every night at 9:00 PM Manila time, GitHub Actions runs the entire suite on the free tier,",
            "archives 90-day CSV audit trails, and delivers the HTML briefing straight to the owner's Gmail inbox—",
            "turning 45 minutes of nightly bookkeeping into zero manual work.",
        ],
    },
]


def synthesize_elevenlabs(text: str, out_mp3: Path, api_key: str, voice_id: str) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.48,
            "similarity_boost": 0.82,
            "style": 0.15,
            "use_speaker_boost": True,
        },
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    out_mp3.write_bytes(resp.content)


async def synthesize_edge_tts(text: str, out_mp3: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice, rate="+2%")
    await communicate.save(str(out_mp3))


def probe_duration(media_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def pad_and_normalize_scene(raw_mp3: Path, padded_wav: Path) -> tuple[float, float]:
    raw_dur = probe_duration(raw_mp3)
    # Add PRE_PAD_SEC silence at start and POST_PAD_SEC silence at end
    delay_ms = int(round(PRE_PAD_SEC * 1000))
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(raw_mp3),
        "-af",
        f"adelay={delay_ms}|{delay_ms},apad=pad_dur={POST_PAD_SEC},aformat=sample_rates=48000:channel_layouts=stereo",
        "-c:a",
        "pcm_s16le",
        str(padded_wav),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    padded_dur = probe_duration(padded_wav)
    return raw_dur, padded_dur


def format_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    minutes = rem // 60_000
    rem %= 60_000
    secs = rem // 1000
    ms = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    eleven_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    eleven_voice = os.getenv("ELEVENLABS_VOICE_ID", "nPczCjzI2devNBz1zQrb").strip()  # Brian (deep narration)
    edge_voice = os.getenv("EDGE_TTS_VOICE", "en-US-BrianNeural").strip()

    engine_used = "elevenlabs:eleven_multilingual_v2" if eleven_key else f"neural-tts:{edge_voice}"
    print(f"[voiceover] Using engine: {engine_used}")

    timeline_scenes: list[dict[str, Any]] = []
    padded_wavs: list[Path] = []
    srt_entries: list[str] = []
    srt_counter = 1
    cursor_sec = 0.0

    for scene in SCENES:
        sid = scene["id"]
        raw_mp3 = BUILD_DIR / f"{sid}.mp3"
        padded_wav = BUILD_DIR / f"{sid}_padded.wav"

        if eleven_key:
            try:
                synthesize_elevenlabs(scene["narration"], raw_mp3, eleven_key, eleven_voice)
            except Exception as exc:
                print(f"[voiceover] ElevenLabs failed on {sid} ({exc}); falling back to {edge_voice}")
                asyncio.run(synthesize_edge_tts(scene["narration"], raw_mp3, edge_voice))
                engine_used = f"neural-tts:{edge_voice} (fallback)"
        else:
            asyncio.run(synthesize_edge_tts(scene["narration"], raw_mp3, edge_voice))

        raw_dur, padded_dur = pad_and_normalize_scene(raw_mp3, padded_wav)
        # Snap scene duration to an exact frame multiple at 30 FPS
        frame_count = int(round(padded_dur * FPS))
        exact_scene_dur = frame_count / FPS

        # Compute phrase-level caption timings proportional to character count inside the active speech window
        phrases: list[str] = scene["phrases"]
        total_chars = sum(len(p) for p in phrases)
        speech_start = cursor_sec + PRE_PAD_SEC
        speech_window = max(0.5, exact_scene_dur - PRE_PAD_SEC - POST_PAD_SEC)

        phrase_items: list[dict[str, Any]] = []
        p_cursor = speech_start
        for idx, phrase_text in enumerate(phrases):
            share = len(phrase_text) / total_chars if total_chars else (1.0 / len(phrases))
            p_dur = speech_window * share
            p_end = (speech_start + speech_window) if idx == len(phrases) - 1 else (p_cursor + p_dur)
            phrase_items.append(
                {
                    "text": phrase_text,
                    "global_start_sec": round(p_cursor, 3),
                    "global_end_sec": round(p_end, 3),
                    "local_start_sec": round(p_cursor - cursor_sec, 3),
                    "local_end_sec": round(p_end - cursor_sec, 3),
                }
            )
            srt_entries.append(
                f"{srt_counter}\n{format_srt_time(p_cursor)} --> {format_srt_time(p_end)}\n{phrase_text}\n"
            )
            srt_counter += 1
            p_cursor = p_end

        scene_meta = {
            "id": sid,
            "index": scene["index"],
            "chapter": scene["chapter"],
            "title": scene["title"],
            "narration": scene["narration"],
            "raw_speech_duration_sec": round(raw_dur, 3),
            "duration_sec": round(exact_scene_dur, 4),
            "frame_count": frame_count,
            "start_sec": round(cursor_sec, 4),
            "end_sec": round(cursor_sec + exact_scene_dur, 4),
            "audio_file": padded_wav.name,
            "phrases": phrase_items,
        }
        timeline_scenes.append(scene_meta)
        padded_wavs.append(padded_wav)
        print(
            f"[voiceover] {sid}: speech={raw_dur:.2f}s | total={exact_scene_dur:.2f}s ({frame_count} frames)"
        )
        cursor_sec += exact_scene_dur

    # Concatenate all padded wavs and apply broadcast loudness normalization
    concat_list_path = BUILD_DIR / "concat_audio.txt"
    concat_lines = [f"file '{w.as_posix()}'" for w in padded_wavs]
    concat_list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    master_wav = BUILD_DIR / "narration_full.wav"
    cmd_concat = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(master_wav),
    ]
    subprocess.run(cmd_concat, capture_output=True, check=True)
    master_dur = probe_duration(master_wav)

    total_frames = sum(s["frame_count"] for s in timeline_scenes)
    timeline_data = {
        "fps": FPS,
        "width": 1920,
        "height": 1080,
        "engine": engine_used,
        "total_frames": total_frames,
        "total_duration_sec": round(total_frames / FPS, 4),
        "master_audio_duration_sec": round(master_dur, 4),
        "master_audio_path": str(master_wav),
        "scenes": timeline_scenes,
    }

    timeline_path = BUILD_DIR / "timeline.json"
    timeline_path.write_text(json.dumps(timeline_data, indent=2), encoding="utf-8")

    srt_path = OUTPUT_DIR / "ely_ops_demo_1080p.srt"
    srt_path.write_text("\n".join(srt_entries), encoding="utf-8")

    print(
        f"[voiceover] Complete! Total duration: {timeline_data['total_duration_sec']:.2f}s "
        f"({total_frames} frames @ {FPS}fps) -> {timeline_path}"
    )


if __name__ == "__main__":
    main()
