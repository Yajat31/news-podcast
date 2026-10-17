"""Sarvam Pod core: Sarvam API calls and route handlers. Served by app.py (locally and on Vercel)."""
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import httpx
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError

ENV_FILE = Path(__file__).resolve().parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_client = None


def client() -> SarvamAI:
    global _client
    if _client is None:
        key = os.environ.get("SARVAM_API_KEY")
        if not key:
            raise RuntimeError("SARVAM_API_KEY is not set on the server.")
        _client = SarvamAI(api_subscription_key=key, timeout=60)
    return _client


def retry(fn, *args, tries=3, **kwargs):
    """Retry Sarvam calls on network blips (DNS, resets, timeouts), rate limits and 5xx."""
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except (httpx.TransportError, ApiError) as e:
            transient = isinstance(e, httpx.TransportError) or e.status_code in (429, 500, 502, 503, 504)
            if not transient or attempt == tries - 1:
                raise
            time.sleep(0.8 * 2**attempt)


def friendly(e: Exception) -> str:
    if isinstance(e, (httpx.TransportError, urllib.error.URLError)):
        return "Can't reach the internet / Sarvam AI right now. Check your connection and try again."
    if isinstance(e, ApiError):
        if e.status_code == 429:
            return "Sarvam AI rate limit hit. Wait a few seconds and try again."
        if e.status_code in (401, 403):
            return "Sarvam AI rejected the API key."
        return f"Sarvam AI error {e.status_code}: {e.body}"
    return str(e)


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
    resp = retry(
        client().chat.completions,
        model="sarvam-105b-conversations",
        temperature=0.8,
        max_tokens=6000,
        response_format={"type": "json_object"},
        request_options={"timeout_in_seconds": 40},
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
    resp = retry(
        client().text_to_speech.convert,
        text=text[:2500], language_code=lang_code, speaker=HOSTS[speaker][1], model="bulbul:v3", output_audio_codec="mp3",
        request_options={"timeout_in_seconds": 25},
    )
    return resp.audios[0]


def to_english(text: str) -> str | None:
    try:
        en = retry(client().text.translate, input=text[:1000], source_language_code="auto", target_language_code="en-IN",
                   request_options={"timeout_in_seconds": 15}).translated_text
    except Exception:
        return None
    # The translator occasionally appends unrelated text after an emoji (e.g. "🚨 Your order has been placed."); cut it off.
    return re.split(r"[\U0001F000-\U0001FAFF☀-➿]", en)[0].strip() or None


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


# ---------- API routes: each takes the request body and headers and returns a JSON-able dict ----------

def route_script(body: dict, headers) -> dict:
    source = str(body.get("source", "")).strip()
    if not source:
        raise ValueError("Give me a topic, a link, or some text")
    if re.match(r"https?://\S+$", source):
        source = fetch_article(source)
    _, lang_desc = LANGUAGES.get(body.get("language"), LANGUAGES["hinglish"])
    turns = max(4, min(int(body.get("turns", 10)), 16))
    script = write_script(source, lang_desc, turns, body.get("format", "podcast"))
    lines = [{"speaker": l["speaker"], "host": HOSTS[l["speaker"]][0], "text": l["text"]} for l in script["lines"]]
    return {"title": script.get("title") or "Untitled episode", "lines": lines}


def route_line(body: dict, headers) -> dict:
    text, speaker, language = str(body.get("text", "")), body.get("speaker"), body.get("language", "hinglish")
    if not text.strip() or speaker not in HOSTS:
        raise ValueError("Bad line")
    lang_code = LANGUAGES.get(language, LANGUAGES["hinglish"])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        audio = pool.submit(speak, text, speaker, lang_code)
        en = pool.submit(to_english, text) if language != "en-IN" else None
        return {"audio": audio.result(), "en": en.result() if en else None}


def route_transcribe(audio: bytes, headers) -> dict:
    mime = headers.get("Content-Type", "audio/webm")
    ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "mp4" if "mp4" in mime else "wav"
    r = retry(
        client().speech_to_text.transcribe,
        file=(f"topic.{ext}", audio, mime.split(";")[0] or "audio/webm"),
        model="saaras:v3", mode="transcribe", language_code="unknown",
    )
    return {"transcript": r.transcript, "language_code": r.language_code}


ROUTES = {"script": (route_script, False), "line": (route_line, False), "transcribe": (route_transcribe, True)}


def handle(name: str, data: bytes, headers) -> tuple[int, dict]:
    """Run one API route and return (status code, JSON-able result)."""
    fn, raw = ROUTES[name]
    try:
        return 200, fn(data if raw else json.loads(data or b"{}"), headers)
    except ValueError as e:
        return 400, {"error": str(e)}
    except Exception as e:
        return 502, {"error": friendly(e)}
