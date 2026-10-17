# Sarvam Pod

Turn any topic, news link or voice note into a captioned two-host show in 11 Indian languages, built on [Sarvam AI](https://docs.sarvam.ai).

**Live:** https://sarvam-pod.vercel.app

Arjun and Riya write the script, voice it, and present it as an animated video. The video has word-by-word native-script captions and English subtitles, and you can export it as a YouTube, Reels or feed video.

## Features

- **Input:** type a topic, paste an article or news URL, or **speak** in any Indian language. Sarvam detects the language and the show is made in that language.
- **Formats:** 🎙 Podcast · 📰 News bulletin · 🧸 Kids story · ⚔️ Debate
- **Languages:** Hinglish, Hindi, English, Tamil, Telugu, Bengali, Marathi, Kannada, Malayalam, Gujarati, Punjabi
- **Video player:** animated background, speaking-host avatars, a live audio waveform, karaoke captions and English subtitles
- **Export:** video in 16:9 / 9:16 / 1:1 (recorded in the browser), or the full episode as MP3
- **Streaming feel:** the transcript appears in about 2 seconds and playback starts as soon as the first line is voiced

## Sarvam APIs used

| Step | API | Model |
|---|---|---|
| Voice note → text + language detection | Speech-to-Text | `saaras:v3` |
| Script writing (JSON) | Chat Completions | `sarvam-105b-conversations` |
| Voices (Arjun = `shubh`, Riya = `ritu`) | Text-to-Speech | `bulbul:v3` (MP3) |
| English subtitles | Translate | default (`auto` → `en-IN`) |

## How it works

```
browser ──POST /api/script──▶ Sarvam-105B writes {title, lines[]}         (~2 s)
        ──POST /api/line ×N─▶ Bulbul TTS + Translate per line, 4 at a time (~3 s each)
        ──POST /api/transcribe▶ Saaras STT for the mic input
```

The browser does the rest: playback, the canvas video, karaoke timing, MP3 stitching and video export (`canvas.captureStream` + `MediaRecorder`).

- `core.py`: Sarvam calls, prompts, retries and friendly errors, article fetching, route handlers
- `app.py`: a dependency-free WSGI app (serves `index.html` and `/api/*`); Vercel runs it directly
- `index.html`: the whole frontend (no build step)

## Run locally

```bash
uv venv && uv pip install -r requirements.txt   # or: python -m venv .venv && pip install -r requirements.txt
echo "SARVAM_API_KEY=your_key_here" > .env
.venv/bin/python app.py                          # http://localhost:8000
```

`hello_sarvam.py` is a one-call sanity check of your key.

## Deploy to Vercel

```bash
vercel link
vercel env add SARVAM_API_KEY production
vercel deploy --prod
```

`.vercelignore` keeps `.env` and `.venv` out of the upload.

## Notes

- Anyone with the deployed URL spends your Sarvam credits. Take the deployment down or rotate the key after demos.
- Video export records in real time. Keep the tab visible while it records.
- Some news sites block scripted downloads. If a link fails, paste the article text instead.
