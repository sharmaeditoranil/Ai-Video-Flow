"""
AI Video Flow - Smart AI audio (background music and voiceover)

Generation runs on ElevenLabs through the license server (<server>/api/v1/audio/...), the same way
Claude calls do: the app sends its license key + machine id, the server checks the Smart AI pack,
applies the admin's daily limit and adds the real API key. The MP3 comes back and is saved locally.
"""

import os
import json
import urllib.request
import urllib.error

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


def transcribe(audio_path: str) -> dict:
    """Words of a voiceover with start / end seconds ({"text", "words": [{"text", "start", "end"}]}).
    The file is sent as a small mono MP3 to the server, which runs ElevenLabs speech-to-text."""
    import subprocess
    import tempfile
    from packages.ai_engine.audio_io import ffmpeg_path
    key, machine_id = license_manager.ai_credentials()
    if not key:
        raise AIAudioError("Timing text to the voiceover needs a Smart AI pack (Pricing page).")
    with tempfile.TemporaryDirectory() as tmp:
        small = os.path.join(tmp, "voiceover.mp3")
        proc = subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "22050",
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
    return data
