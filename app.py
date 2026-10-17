"""Sarvam Pod: turn any topic or article into a two-host podcast in an Indian language."""
import base64
import io
import json
import os
import re
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sarvamai import SarvamAI

ROOT = Path(__file__).parent
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

LANGUAGES = {
    "hinglish": ("hi-IN", "Hinglish: mostly Hindi sentences written in Roman script, sprinkled with English words, like Mumbai friends talk (e.g. 'Yaar, ye UPI ne literally game hi change kar diya')"),
    "hi-IN": ("hi-IN", "Hindi (Devanagari script)"),
    "en-IN": ("en-IN", "Indian English"),
    "ta-IN": ("ta-IN", "Tamil (Tamil script)"),
    "te-IN": ("te-IN", "Telugu (Telugu script)"),
    "bn-IN": ("bn-IN", "Bengali (Bengali script)"),
    "mr-IN": ("mr-IN", "Marathi (Devanagari script)"),
    "kn-IN": ("kn-IN", "Kannada (Kannada script)"),
    "ml-IN": ("ml-IN", "Malayalam (Malayalam script)"),
    "gu-IN": ("gu-IN", "Gujarati (Gujarati script)"),
    "pa-IN": ("pa-IN", "Punjabi (Gurmukhi script)"),
}
HOSTS = {"A": ("Arjun", "shubh"), "B": ("Riya", "ritu")}

FORMATS = {
    "podcast": "a lively two-host podcast. Arjun is curious, asks sharp questions and is a bit funny; "
               "Riya is the explainer, warm, and uses vivid desi analogies. Open with a hook, end with a one-line takeaway and a sign-off.",
    "news": "a crisp TV news bulletin. Arjun is the studio anchor who introduces and asks short follow-ups; "
            "Riya is the field reporter giving the facts, numbers and context. Start with a headline, end with 'back to the studio' and a sign-off.",
    "story": "a bedtime story for kids aged 5-9. Riya is the storyteller narrating with drama and simple words; "
             "Arjun is the excited kid listener who reacts and asks 'phir kya hua?'-style questions. End with a gentle moral.",
    "debate": "a friendly but heated debate. Arjun argues FOR the idea, Riya argues AGAINST it, each with concrete examples "
              "and rebuttals. End with both agreeing on one thing and a sign-off.",
}

SCRIPT_PROMPT = """You write scripts for {format}
Write the ENTIRE script in {lang}. Keep technical terms in English where people naturally would.
Length: {turns} turns total, alternating, starting with Arjun. Each turn 1-3 short spoken sentences, no stage directions, no emojis, no markdown.
Return ONLY JSON: {{"title": "...", "lines": [{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}]}}"""


def write_script(source: str, lang_desc: str, turns: int, fmt: str = "podcast", tries: int = 2) -> dict:
    try:
        return _write_script(source, lang_desc, turns, fmt)
    except (ValueError, KeyError, TypeError, AttributeError):
        if tries <= 1:
            raise
        return write_script(source, lang_desc, turns, fmt, tries - 1)


def _write_script(source: str, lang_desc: str, turns: int, fmt: str) -> dict:
    resp = client.chat.completions(
        model="sarvam-105b-conversations",
        temperature=0.8,
        max_tokens=6000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SCRIPT_PROMPT.format(format=FORMATS.get(fmt, FORMATS["podcast"]), lang=lang_desc, turns=turns)},
            {"role": "user", "content": f"Make an episode about this:\n\n{source[:8000]}\n\n"
                f"IMPORTANT: every line of dialogue must be written in {lang_desc}, not English, using exactly the script named there."},
        ],
    )
    raw = resp.choices[0].message.content or ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("Model did not return a script")
    script = json.loads(match.group(0))
    lines = []
    for l in script["lines"]:
        if isinstance(l, (list, tuple)) and len(l) == 2:
            l = {"speaker": l[0], "text": l[1]}
        if isinstance(l, dict) and l.get("speaker") in HOSTS and str(l.get("text", "")).strip():
            lines.append({"speaker": l["speaker"], "text": str(l["text"]).strip()})
    if len(lines) < 2:
        raise ValueError("Script too short")
    script["lines"] = lines
    return script


def speak(text: str, speaker: str, lang_code: str) -> str:
    resp = client.text_to_speech.convert(
        text=text[:2500], language_code=lang_code, speaker=HOSTS[speaker][1], model="bulbul:v3"
    )
    return resp.audios[0]


def to_english(text: str) -> str | None:
    try:
        return client.text.translate(input=text[:1000], source_language_code="auto", target_language_code="en-IN").translated_text
    except Exception:
        return None


def make_episode(source: str, language: str, turns: int, fmt: str = "podcast"):
    """Yield events: the script as soon as it's written, then each line's audio in order."""
    lang_code, lang_desc = LANGUAGES.get(language, LANGUAGES["hinglish"])
    if re.match(r"https?://\S+$", source):
        yield {"type": "status", "text": "Reading the article…"}
        source = fetch_article(source)
    yield {"type": "status", "text": "Arjun & Riya are writing the script…"}
    script = write_script(source, lang_desc, turns, fmt)
    lines = [{"speaker": l["speaker"], "host": HOSTS[l["speaker"]][0], "text": l["text"]} for l in script["lines"]]
    yield {"type": "script", "title": script.get("title") or "Untitled episode", "lines": lines}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(speak, l["text"], l["speaker"], lang_code) for l in lines]
        subs = [pool.submit(to_english, l["text"]) if language != "en-IN" else None for l in lines]
        audios = []
        for i, f in enumerate(futures):
            audios.append(f.result())
            yield {"type": "audio", "i": i, "audio": audios[-1], "en": subs[i].result() if subs[i] else None}
    yield {"type": "done", "full": stitch(audios)}


class _TextGrabber(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.title, self._in = [], "", None

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "h1", "h2", "title"):
            self._in = tag

    def handle_endtag(self, tag):
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in and data.strip():
            self.parts.append(data.strip())


def fetch_article(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) SarvamPod/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "ignore")
    g = _TextGrabber()
    g.feed(html)
    text = " ".join(g.parts)
    if len(text) < 200:
        raise ValueError("Couldn't read that page. Try pasting the article text instead.")
    return f"Article: {g.title.strip()}\n\n{text[:8000]}"


def transcribe(audio: bytes, mime: str) -> dict:
    ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "mp4" if "mp4" in mime else "wav"
    r = client.speech_to_text.transcribe(
        file=(f"topic.{ext}", audio, mime.split(";")[0] or "audio/webm"),
        model="saaras:v3", mode="transcribe", language_code="unknown",
    )
    return {"transcript": r.transcript, "language_code": r.language_code}


def stitch(clips: list[str]) -> str:
    """Join base64 WAV clips into one base64 WAV with a short pause between turns."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        for i, clip in enumerate(clips):
            with wave.open(io.BytesIO(base64.b64decode(clip))) as r:
                if i == 0:
                    w.setparams(r.getparams())
                    gap = b"\0" * int(r.getframerate() * 0.35) * r.getsampwidth() * r.getnchannels()
                else:
                    w.writeframes(gap)
                w.writeframes(r.readframes(r.getnframes()))
    return base64.b64encode(out.getvalue()).decode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/transcribe":
            try:
                result = transcribe(data, self.headers.get("Content-Type", "audio/webm"))
                return self._send(200, json.dumps(result).encode(), "application/json")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        if self.path != "/api/episode":
            return self._send(404, b"not found", "text/plain")

        # Stream newline-delimited JSON events so the page can start playing before all audio is ready.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            body = json.loads(data)
            source = body.get("source", "").strip()
            if not source:
                raise ValueError("Give me a topic, a link, or some text")
            turns = max(4, min(int(body.get("turns", 10)), 16))
            for event in make_episode(source, body.get("language", "hinglish"), turns, body.get("format", "podcast")):
                self.wfile.write(json.dumps(event).encode() + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.wfile.write(json.dumps({"type": "error", "error": str(e)}).encode() + b"\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Sarvam Pod on http://localhost:{port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
