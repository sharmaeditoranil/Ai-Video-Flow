"""
AI Video Flow - Claude client (AI Smart Premium)

All requests go to the license server's Claude relay (<server>/api/v1/ai). The app authenticates with
its license key + machine id; the server checks the license, applies the admin's model and limits,
and adds the real Anthropic API key. No API key is ever stored on the customer's computer.
"""

import os
import json
import base64
import logging
from typing import Dict, Any, List, Callable, Optional

import anthropic

from packages.licensing.license_manager import license_manager

logger = logging.getLogger("ai_video_flow.cloud_ai")

# The relay replaces the model with the one chosen in the admin portal; this is only the default
MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ASSISTANT_SYSTEM = (
    "You are Smart AI, the editing assistant inside AI Video Flow, a desktop app for wedding and event video editors. "
    "You control DaVinci Resolve, Adobe Premiere Pro and Grass Valley EDIUS on the user's computer through the "
    "provided tools, and can analyse footage, music and reference images. Users write in Hindi, Hinglish or "
    "English - reply in the same language and style, briefly and practically. Use tools to actually do what is "
    "asked instead of describing steps. If a tool reports that an editor is not running or a file is missing, "
    "tell the user exactly what to open or select. Ask before deleting or overwriting anything."
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "moment": {"type": "string"},
                    "emotion": {"type": "integer"},
                    "keep": {"type": "boolean"},
                },
                "required": ["index", "moment", "emotion", "keep"],
                "additionalProperties": False,
            },
        },
        "story_note": {"type": "string"},
    },
    "required": ["shots", "story_note"],
    "additionalProperties": False,
}


class CloudAIError(RuntimeError):
    pass


def _error_message(e: anthropic.APIStatusError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error")
    if isinstance(err, str):  # license / limit errors come from the relay itself
        return err
    if isinstance(err, dict) and err.get("message"):
        return err["message"]
    return e.message


class CloudAIClient:
    def _client(self) -> anthropic.Anthropic:
        status = license_manager.get_status()
        if not status["has_credits"]:
            raise CloudAIError("Smart AI needs a Smart AI pack with weddings left (Pricing page).")
        key, machine_id = license_manager.ai_credentials()
        return anthropic.Anthropic(
            api_key=key,
            base_url=f"{license_manager.server_url}/api/v1/ai",
            # Some hosting setups strip the Authorization header the SDK uses for custom base URLs
            default_headers={"x-machine-id": machine_id, "x-api-key": key},
            timeout=300.0,
            max_retries=1,
        )

    def _create(self, **params):
        try:
            return self._client().beta.messages.create(
                model=MODEL, betas=[FALLBACK_BETA], fallbacks="default", **params
            )
        except CloudAIError:
            raise
        except anthropic.AuthenticationError as e:
            raise CloudAIError(f"License check failed: {_error_message(e)}")
        except anthropic.PermissionDeniedError as e:
            raise CloudAIError(_error_message(e))
        except anthropic.RateLimitError as e:
            raise CloudAIError(f"Daily AI limit reached. {_error_message(e)}")
        except anthropic.APIStatusError as e:
            raise CloudAIError(f"AI service error ({e.status_code}): {_error_message(e)}")
        except anthropic.APIConnectionError:
            raise CloudAIError("No internet connection to the AI service. Check your network and try again.")

    def get_status(self) -> Dict[str, Any]:
        st = license_manager.get_status()
        return {"available": st["has_credits"], "provider": "Smart AI (via license server)"}

    # ------------------------------------------------------------------
    # AI shot review for the wedding job
    # ------------------------------------------------------------------
    def review_shots(self, thumbnails: List[str], context: str = "") -> Dict[str, Any]:
        """Scores emotion / story value of one JPEG thumbnail per clip. Returns {index: {...}} + story_note."""
        content: List[Dict[str, Any]] = []
        for i, path in enumerate(thumbnails):
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({"type": "text", "text": f"Shot {i}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        content.append({"type": "text", "text": (
            "These are frames from raw wedding/event clips (one frame per clip). For every shot give: index, a short "
            "moment label (e.g. 'bride entry', 'varmala', 'couple portrait', 'crowd', 'decor'), an emotion/story score "
            "0-100 for a cinematic highlight film, and keep=false only for frames that are unusable (blurred, blocked, "
            "accidental). Then one sentence of story advice in story_note."
            + (f"\nEditor's instructions: {context}" if context else "")
        )})
        response = self._create(
            max_tokens=16000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise CloudAIError("The AI declined to review these frames.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise CloudAIError("The AI returned an unreadable review.")
        return {
            "shots": {s["index"]: s for s in data.get("shots", []) if 0 <= s.get("index", -1) < len(thumbnails)},
            "story_note": data.get("story_note", ""),
        }

    # ------------------------------------------------------------------
    # AI audio brief: music prompt + voiceover script for the edit
    # ------------------------------------------------------------------
    def audio_brief(self, mode: str, moments: List[str], story_note: str, instructions: str,
                    music_seconds: int, voice_seconds: int) -> Dict[str, str]:
        """Writes an instrumental music prompt and a short voiceover script that fit the footage."""
        response = self._create(
            max_tokens=4000,
            messages=[{"role": "user", "content": (
                f"An editor is cutting a {mode.replace('_', ' ')} video. Key moments in the footage: "
                f"{', '.join(moments[:20]) or 'not reviewed'}. "
                + (f"Story advice: {story_note}. " if story_note else "")
                + (f"Editor's instructions: {instructions}. " if instructions else "")
                + f"\n1. music_prompt: an English prompt for an AI music generator for a {music_seconds} s instrumental "
                "track that fits this video - genre, mood, instruments (Indian instruments where it suits), tempo in "
                "BPM and how it builds. Never name real artists, songs or films.\n"
                f"2. voiceover_script: a voiceover of about {voice_seconds} seconds spoken "
                f"(roughly {int(voice_seconds * 2.3)} words) for the opening of the video, warm and natural. Write it in "
                "Hindi (Devanagari) unless the editor's instructions ask for English or Hinglish. Only the spoken "
                "words - no stage directions, names in brackets or emojis."
            )}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {"music_prompt": {"type": "string"}, "voiceover_script": {"type": "string"}},
                "required": ["music_prompt", "voiceover_script"],
                "additionalProperties": False,
            }}},
        )
        if response.stop_reason == "refusal":
            raise CloudAIError("The AI declined to write the audio brief.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise CloudAIError("The AI returned an unreadable audio brief.")
        return {"music_prompt": data.get("music_prompt", ""), "voiceover_script": data.get("voiceover_script", "")}

    # ------------------------------------------------------------------
    # AI assistant (tool use against the local editing tools)
    # ------------------------------------------------------------------
    def assistant_turn(
        self,
        history: List[Dict[str, Any]],
        user_text: str,
        tools: List[Dict[str, Any]],
        execute: Callable[[str, Dict[str, Any]], Any],
        on_event: Optional[Callable[[str, str], None]] = None,
        max_steps: int = 12,
    ) -> str:
        """Runs one user turn to completion. `history` is extended in place so the chat keeps context."""
        history.append({"role": "user", "content": user_text})
        for _ in range(max_steps):
            response = self._create(
                max_tokens=16000,
                system=ASSISTANT_SYSTEM,
                tools=tools,
                messages=history,
                cache_control={"type": "ephemeral"},
            )
            history.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return "Sorry, I can't help with that request."
            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n(The answer was cut short - ask me to continue.)"
                return text or "Done."

            results = []
            for tu in tool_uses:
                if on_event:
                    on_event("tool", tu.name)
                try:
                    out = execute(tu.name, dict(tu.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": json.dumps(out, ensure_ascii=False, default=str)[:20000]})
                except Exception as e:
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"Error: {e}", "is_error": True})
            history.append({"role": "user", "content": results})
        return "I stopped after many steps. Tell me what to do next."


cloud_ai_client = CloudAIClient()
