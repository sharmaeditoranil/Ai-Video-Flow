"""
AI Video Flow - Smart AI audio (background music and voiceover)

Generation runs on ElevenLabs through the license server (<server>/api/v1/audio/...), the same way
Claude calls do: the app sends its license key + machine id, the server checks the Smart AI pack,
applies the admin's daily limit and adds the real API key. The MP3 comes back and is saved locally.
"""

import os
import re
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from packages.licensing.license_manager import license_manager, APP_VERSION, _urlopen


class AIAudioError(RuntimeError):
    pass


def _generate(kind: str, payload: dict, out_path: str) -> str:
    key, machine_id = license_manager.ai_credentials()
    if not key:
        raise AIAudioError("Smart AI audio needs a Smart AI pack (Pricing page).")
    req = urllib.request.Request(
        f"{license_manager.server_url}/api/v1/audio/{kind}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key, "x-machine-id": machine_id,
                 "User-Agent": f"AIVideoFlow/{APP_VERSION}"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=300) as resp:
            data = resp.read()
            if not resp.headers.get("Content-Type", "").startswith("audio/") or len(data) < 1000:
                raise AIAudioError("The server did not return audio.")
    except urllib.error.HTTPError as he:
        try:
            message = json.loads(he.read().decode("utf-8")).get("error")
        except Exception:
            message = None
        if he.code == 429:
            raise AIAudioError("Daily AI audio limit reached. Try again tomorrow.")
        raise AIAudioError(message or f"AI audio server error (HTTP {he.code}).")
    except urllib.error.URLError:
        raise AIAudioError("No internet connection to the AI audio service.")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def generate_music(prompt: str, seconds: int, out_path: str) -> str:
    """Instrumental background music of `seconds` length (10-300 s)."""
    return _generate("music", {"prompt": prompt, "seconds": int(max(10, min(300, seconds)))}, out_path)


def generate_voiceover(text: str, out_path: str) -> str:
    """Spoken voiceover of `text` (Hindi, Hinglish or English) in the voice chosen in the admin portal."""
    return _generate("voice", {"text": text[:2500]}, out_path)


def duration(path: str) -> float:
    from packages.ai_engine.audio_io import load_audio
    samples, sr = load_audio(path, 8000)
    return len(samples) / float(sr)


def transcribe(audio_path: str, start: float = 0.0, end: Optional[float] = None) -> dict:
    """Words of a voiceover with start / end seconds ({"text", "words": [{"text", "start", "end"}]}).
    The file is sent as a small mono MP3 to the server, which runs ElevenLabs speech-to-text.
    start / end: only that part of the file is sent; word times stay relative to the whole file."""
    import subprocess
    import tempfile
    from packages.ai_engine.audio_io import ffmpeg_path
    key, machine_id = license_manager.ai_credentials()
    if not key:
        raise AIAudioError("Timing text to the voiceover needs a Smart AI pack (Pricing page).")
    with tempfile.TemporaryDirectory() as tmp:
        small = os.path.join(tmp, "voiceover.mp3")
        span = (["-ss", f"{max(0.0, start):.3f}"] if start else []) + (["-t", f"{max(0.1, end - start):.3f}"] if end else [])
        proc = subprocess.run([ffmpeg_path(), "-v", "error", "-y", *span, "-i", audio_path, "-vn", "-ac", "1", "-ar", "22050",
                               "-b:a", "64k", small], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not os.path.isfile(small):
            raise AIAudioError("Could not read the voiceover file.")
        with open(small, "rb") as f:
            body = f.read()
    req = urllib.request.Request(
        f"{license_manager.server_url}/api/v1/audio/transcribe", data=body, method="POST",
        headers={"Content-Type": "audio/mpeg", "x-api-key": key, "x-machine-id": machine_id,
                 "User-Agent": f"AIVideoFlow/{APP_VERSION}"})
    try:
        with _urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as he:
        try:
            message = json.loads(he.read().decode("utf-8")).get("error")
        except Exception:
            message = None
        if he.code == 429:
            raise AIAudioError("Daily transcription limit reached. Try again tomorrow.")
        raise AIAudioError(message or f"Transcription server error (HTTP {he.code}).")
    except urllib.error.URLError:
        raise AIAudioError("No internet connection to the transcription service.")
    if not data.get("success") or not data.get("words"):
        raise AIAudioError(data.get("error") or "No speech was found in the voiceover.")
    if start:
        for w in data["words"]:
            w["start"] = round(float(w.get("start", 0)) + start, 3)
            w["end"] = round(float(w.get("end", 0)) + start, 3)
    return data


# ======================================================================================================
# Vows & speeches (STEP 6): transcription, lines, the most emotional lines, captions
# Smart AI users: the license server's speech-to-text (better Hindi, nothing to download).
# Everyone else / offline: faster-whisper on this computer; the model downloads once on first use.
# ======================================================================================================

LOCAL_WHISPER_MODEL = "small"     # multilingual: Hindi, English, Hinglish, Punjabi (~480 MB, downloaded once)
_WHISPER: Dict[str, Any] = {}


def whisper_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output", ".models", "whisper")


def local_transcription_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def local_model_ready(model_size: str = LOCAL_WHISPER_MODEL) -> bool:
    root = whisper_dir()
    return os.path.isdir(root) and any(model_size in d for d in os.listdir(root))


def transcribe_local(path: str, start: float = 0.0, end: Optional[float] = None,
                     model_size: str = LOCAL_WHISPER_MODEL, download_root: Optional[str] = None) -> dict:
    """Same shape as transcribe(): {"text", "words": [{"text", "start", "end"}], "language", "engine"}.
    Runs on the CPU (int8). The audio is decoded by the app's own ffmpeg, so no extra codecs are needed."""
    try:
        from faster_whisper import WhisperModel
    except Exception:
        raise AIAudioError("Offline transcription is not installed in this version of the app.")
    from packages.ai_engine.audio_io import load_audio
    y, sr = load_audio(path, 16000)
    a = int(max(0.0, start) * sr)
    b = int(end * sr) if end else len(y)
    seg = y[a:b]
    if seg.size < sr // 2:
        return {"text": "", "words": [], "language": None, "engine": "local"}
    key = f"{model_size}|{download_root or whisper_dir()}"
    model = _WHISPER.get(key)
    if model is None:
        os.makedirs(download_root or whisper_dir(), exist_ok=True)
        model = WhisperModel(model_size, device="cpu", compute_type="int8", download_root=download_root or whisper_dir())
        _WHISPER[key] = model
    segments, info = model.transcribe(seg, beam_size=5, vad_filter=True, word_timestamps=True,
                                      condition_on_previous_text=False)
    words = []
    for sgm in segments:
        for w in (sgm.words or []):
            text = (w.word or "").strip()
            if text:
                words.append({"text": text, "start": round(float(w.start) + start, 3), "end": round(float(w.end) + start, 3)})
    return {"text": " ".join(w["text"] for w in words), "words": words, "language": getattr(info, "language", None),
            "engine": "local"}


def split_lines(words: List[Dict[str, Any]], max_gap: float = 0.6, max_words: int = 14) -> List[Dict[str, Any]]:
    """Spoken lines: a new line after a pause, at the end of a sentence, or after max_words words."""
    lines: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []
    for w in words:
        if cur and (float(w["start"]) - float(cur[-1]["end"]) > max_gap or len(cur) >= max_words):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!।]$", w["text"]):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        text = " ".join(w["text"] for w in ln).strip()
        text = re.sub(r"\s+([,.?!।])", r"\1", text)
        if text:
            out.append({"text": text, "start": round(float(ln[0]["start"]), 2), "end": round(float(ln[-1]["end"]), 2)})
    return out


EMOTION_WORDS = [
    "love", "promise", "forever", "always", "together", "family", "thank", "blessed", "happy", "heart", "life", "dream",
    "mom", "mummy", "maa", "papa", "dad", "beti", "beta", "pyaar", "pyar", "hamesha", "zindagi", "khush", "dil", "saath",
    "rishta", "dua", "aashirwad", "shukriya", "sapna", "kasam", "vaada", "wada", "jaan", "meri", "tumhare", "aapke",
    "प्यार", "हमेशा", "ज़िंदगी", "जिंदगी", "खुश", "दिल", "साथ", "परिवार", "माँ", "पापा", "बेटी", "आशीर्वाद", "वादा",
    "धन्यवाद", "सपना", "रिश्ता", "ਪਿਆਰ", "ਹਮੇਸ਼ਾ", "ਖੁਸ਼", "ਦਿਲ",
]
FILLER = {"um", "umm", "uh", "hmm", "haan", "ok", "okay", "acha", "accha", "theek", "so", "like", "yeah"}


def score_line(text: str, seconds: float) -> float:
    """0-100: how well a spoken line works in a wedding film (local rule, used without Smart AI)."""
    words = [w.strip(".,?!।").lower() for w in text.split()]
    if not words:
        return 0.0
    n = len(words)
    s = 40.0
    s += 20 if 4 <= n <= 14 else (5 if 3 <= n <= 18 else -20)
    s += min(30.0, 10.0 * sum(1 for w in words if any(k in w for k in EMOTION_WORDS)))
    s -= 12.0 * sum(1 for w in words if w in FILLER) / max(1, n) * 5
    if seconds > 7.0:
        s -= (seconds - 7.0) * 4
    if seconds < 1.2:
        s -= 15
    return round(max(0.0, min(100.0, s)), 1)


def choose_lines_local(candidates: List[Dict[str, Any]], max_lines: int = 5) -> List[Dict[str, Any]]:
    """candidates: [{"id", "text", "start", "end", "bonus"}]. Best lines, at most one per clip id."""
    ranked = sorted(candidates, key=lambda c: score_line(c["text"], c["end"] - c["start"]) + float(c.get("bonus", 0)), reverse=True)
    out, seen = [], set()
    for c in ranked:
        if c["id"] in seen or score_line(c["text"], c["end"] - c["start"]) < 45:
            continue
        out.append(c)
        seen.add(c["id"])
        if len(out) >= max_lines:
            break
    return out


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(captions: List[Dict[str, Any]], out_path: str) -> str:
    """captions: [{"start", "end", "text"}] in timeline seconds."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(sorted(captions, key=lambda c: c["start"]), 1):
            f.write(f"{i}\n{_srt_time(c['start'])} --> {_srt_time(c['end'])}\n{c['text'].strip()}\n\n")
    return out_path
