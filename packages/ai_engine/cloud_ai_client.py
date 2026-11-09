"""
AI Video Flow - Claude client (AI Smart Premium)

All requests go to the license server's Claude relay (<server>/api/v1/ai). The app authenticates with
its license key + machine id; the server checks the license, applies the admin's model and limits,
and adds the real Anthropic API key. No API key is ever stored on the customer's computer.
"""

from __future__ import annotations

import os
import json
import base64
import logging
from typing import Dict, Any, List, Callable, Optional

try:
    import anthropic
except ImportError:  # the Android Reels app ships without the SDK; Smart AI then reports itself unavailable
    anthropic = None

from packages.licensing.license_manager import license_manager

logger = logging.getLogger("ai_video_flow.cloud_ai")

# The relay replaces the model with the one chosen in the admin portal; this is only the default
MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ASSISTANT_SYSTEM = (
    "You are Smart AI, the world-class Creative Director, Video Editor and Motion Graphics Producer inside AI Video Flow. "
    "You control DaVinci Resolve, Adobe Premiere Pro, Grass Valley EDIUS, and the HyperFrames Motion Graphics engine "
    "on the user's computer through tools. You craft high-impact Commercial Video Ads, Viral Reels/Shorts, Motion Graphics "
    "from PSD layers and PNG images, beat-synced music videos, and cinematic wedding films. "
    "When users give creative prompts or reference videos (YouTube links, reference MP4s), extract their rhythm, color look, "
    "and kinetic motion design to deliver matching professional edits. "
    "Users write in Hindi, Hinglish or English - reply in the exact same language and tone with clear, enthusiastic, practical "
    "guidance. Perform actions directly using your tools whenever requested. Ask before deleting or overwriting anything."
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

CREATIVE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "creative_style": {"type": "string"},
        "pacing_bpm": {"type": "integer"},
        "storyboard": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_index": {"type": "integer"},
                    "scene_role": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "motion_effect": {"type": "string"},
                    "text_hook": {"type": "string"},
                    "beat_drop": {"type": "boolean"},
                },
                "required": ["asset_index", "scene_role", "duration_seconds", "motion_effect", "text_hook", "beat_drop"],
                "additionalProperties": False,
            },
        },
        "director_note": {"type": "string"},
    },
    "required": ["title", "creative_style", "pacing_bpm", "storyboard", "director_note"],
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


_STR = {"type": "string"}
_NUM = {"type": "number"}
MOTION_AD_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STR,
        "theme": {"type": "string", "enum": ["gold_dark", "navy_gold", "purple_neon"]},
        "brand_name": _STR,
        "brand_sub": _STR,
        "badge": _STR,
        "subtitles": {"type": "boolean"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": _NUM,
                    "end": _NUM,
                    "image": {"type": "integer"},
                    "image_side": {"type": "string", "enum": ["left", "center", "right"]},
                    "flash": {"type": "boolean"},
                    "elements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "at": _NUM,
                                "anim": {"type": "string", "enum": ["pop", "rise", "fade", "slide"]},
                                "type": {"type": "string", "enum": ["label", "headline", "gold", "chip", "card", "icon_card", "image",
                                                                     "grid", "timeline", "logos", "clock", "button", "progress", "lower_third"]},
                                "text": _STR,
                                "text2": _STR,
                                "highlight": _STR,
                                "size": {"type": "string", "enum": ["s", "m", "l", "xl"]},
                                "icon": {"type": "string", "enum": ["none", "camera", "film", "clock", "check", "hourglass", "target", "spark",
                                                                     "dot", "ticket", "play", "star", "users", "calendar", "rupee", "mic", "bolt", "heart"]},
                                "gold": {"type": "boolean"},
                                "image": {"type": "integer"},
                                "items": {"type": "array", "items": _STR},
                                "x": _NUM,
                                "y": _NUM,
                            },
                            "required": ["at", "anim", "type", "text", "text2", "highlight", "size", "icon", "gold", "image", "items", "x", "y"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["start", "end", "image", "image_side", "flash", "elements"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "theme", "brand_name", "brand_sub", "badge", "subtitles", "scenes"],
    "additionalProperties": False,
}

MOTION_AD_GUIDE = """You are a senior motion-graphics designer making a social ad with an animation engine. You design
SCENES as JSON; the engine draws them. Everything must be timed to the voiceover: every scene starts on a spoken
word and every element appears ("at") on the word it illustrates, using the timestamps below.

CANVAS: {aspect} ({w}x{h}). x / y are the CENTRE of an element as fractions of the frame (0..1).
Reserved areas: the brand header is the top 0.07; subtitles (when on) sit at y 0.88-0.95 - keep elements out of both.
When a scene shows a photo of a person ("image" >= 0, drawn as a cut-out from the bottom), it covers the lower
~55% of a 9:16 frame (lower ~65% for 1:1 / 16:9, or the chosen side): put that scene's text ABOVE it (y 0.12-0.40
on 9:16), or on the other side for image_side left/right. Without a photo the whole frame is free.
Leave >= 0.09 of height between stacked elements; never overlap. Each scene 2-5 elements, max ~6 seconds.

ELEMENT TYPES (text in UPPERCASE Hinglish/English as spoken, short: 1-4 words, except card / headline):
- label: small spaced caption over a block ("AGAR AAP KARTE HAIN"). size s|m.
- headline: big white words, wraps to 2 lines; put the key words in "highlight" (they turn gold).
- gold: huge glowing gold word(s) for the punch line ("AI", "SAMAY!"). Use for 1-2 words.
- chip: pill with an icon; gold=true is a filled gold pill, false is an outlined one.
- card: list row (text = item, text2 = number like "01" or "" for a check mark, icon optional). Stack 2-4 cards
  0.08 apart for lists the speaker counts out, each "at" its word.
- icon_card: square card with a big icon, text = line 1, text2 = line 2. Place two side by side (x 0.3 / 0.7).
- image: one of the photos as a framed card (image = its number, size s|m|l).
- grid: wall of photo tiles where some light up (picking the best photos / footage).
- timeline: an editing timeline with clips filling in and a progress bar; text = its caption.
- logos: software / brand tiles appearing one by one; items = names, e.g. ["Premiere Pro","DaVinci Resolve","EDIUS"].
- clock: ticking clock (time, hours, wait). button: a CTA button that gets clicked (text = "JOIN NOW").
- progress: bar filling to 100% with a caption. lower_third: name (text) + role (text2) badge.
anim: pop (scale overshoot, for punch words), rise (slide up, default), slide (from the left), fade.
Scene options: image = photo number shown big (-1 = none; vary the photos, don't repeat one back to back unless
it is a cut on purpose), flash = a gold flash at the scene start for big moments.

STYLE: premium dark + gold like a top Indian edu-tech / wedding-film ad. Hook in the first 2 seconds, one idea per
scene, the CTA (button / chip with the offer, price, date) near the end and an end card in the last scene.
brand_name / brand_sub / badge: the business, a tagline and a short badge (e.g. "LIVE · ₹21") from the prompt or
the voiceover ("" when unknown). Fill every field; use "", -1, [] or false when it does not apply.
"""

class CloudAIClient:
    def _client(self) -> anthropic.Anthropic:
        status = license_manager.get_status()
        if not status["has_credits"]:
            raise CloudAIError("Smart AI needs a Smart AI pack with weddings left (Pricing page).")
        if anthropic is None:
            raise CloudAIError("Smart AI is not available in this version of the app.")
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
        return {"available": st["has_credits"] and anthropic is not None, "provider": "Smart AI (via license server)"}

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
    # AI Reference-to-Reel Clone Blueprint (matches ref shots + depth text + Hinglish captions)
    # ------------------------------------------------------------------
    def plan_reel_clone(
        self,
        ref_keyframes: List[str],
        user_thumbnails: List[str],
        instructions: str = "",
        total_duration: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Compares Reference Reel frames with User's raw clip frames to produce:
        - detected_style: talking_depth_captions | cinema_90_rotated | speed_ramp_luxury | beat_sync_montage
        - shot_order: ordered list of user clip indices matching the reference progression
        - depth_hero_text: 2-line behind-subject impact text (e.g. ['ROUGH CUT', 'READY'])
        - captions: timed Hinglish/Hindi/English pop-up caption lines with highlighted keywords
        """
        content: List[Dict[str, Any]] = []
        for idx, rpath in enumerate(ref_keyframes[:6]):
            if not os.path.isfile(rpath):
                continue
            with open(rpath, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({"type": "text", "text": f"Reference Reel Frame {idx}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

        for idx, upath in enumerate(user_thumbnails[:24]):
            if not os.path.isfile(upath):
                continue
            with open(upath, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({"type": "text", "text": f"User Raw Clip {idx}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

        content.append({"type": "text", "text": (
            f"You are cloning the editing style of the Reference Reel onto the User's Raw Clips (target duration {total_duration:.0f}s).\n"
            "1. detected_style: choose 'talking_depth_captions' if talking head/educational with captions, "
            "'cinema_90_rotated' if 90-degree rotated horizontal cinema wedding shots, "
            "'speed_ramp_luxury' if macro bridal/detail shots with fast-to-slow speed ramps, or 'beat_sync_montage'.\n"
            "2. shot_order: list of User Raw Clip indices (0-based) ordered to mirror the Reference Reel's shot flow (e.g. hand hold -> bangles -> dress -> portrait).\n"
            "3. depth_hero_text: 2 short uppercase impact words/phrases to place in giant text behind the subject's head (e.g. ['ROUGH CUT', 'READY'] or ['ROYAL', 'WEDDING']).\n"
            "4. captions: 3 to 5 punchy Hinglish/Hindi pop-up caption objects {start, end, text, highlight} spanning the reel."
            + (f"\nEditor's instructions: {instructions}" if instructions else "")
        )})

        schema = {
            "type": "object",
            "properties": {
                "detected_style": {"type": "string"},
                "shot_order": {"type": "array", "items": {"type": "integer"}},
                "depth_hero_text": {"type": "array", "items": {"type": "string"}},
                "captions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "text": {"type": "string"},
                            "highlight": {"type": "string"},
                        },
                        "required": ["start", "end", "text", "highlight"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["detected_style", "shot_order", "depth_hero_text", "captions"],
            "additionalProperties": False,
        }
        response = self._create(
            max_tokens=8000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        return json.loads(text)

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
    # AI Creative Plan: Commercial Motion Graphics Ads & Stylized Reels
    # ------------------------------------------------------------------
    def generate_creative_plan(
        self,
        assets_summary: List[Dict[str, Any]],
        user_prompt: str,
        ref_video_or_link: str = "",
        mode: str = "reels"
    ) -> Dict[str, Any]:
        """
        Uses Claude AI to plan a high-impact Motion Graphics Ad / Commercial Video
        tailored to the user's prompt, PSD layers, PNG graphics, and reference video style.
        """
        prompt_content = (
            f"You are the Lead Creative Director and Motion Graphics Producer. The user wants to create a {mode} video/ad.\n"
            f"User Creative Prompt: \"{user_prompt or 'Create an energetic, high-converting motion graphics video'}\"\n"
            + (f"Reference Video / Style Link: {ref_video_or_link}\n" if ref_video_or_link else "")
            + f"Available Media Assets ({len(assets_summary)} items):\n"
            + "\n".join(f"[{i}] {a.get('file_name', 'Asset')} (Type: {a.get('type', 'clip')})" for i, a in enumerate(assets_summary[:30]))
            + "\n\nPlan the complete storyboard cut-by-cut with kinetic motion effects (Ken Burns zoom-in/out, whip pan, pop-in, beat cut), "
            "catchy text overlays / hooks in Hindi/English, and perfect rhythm."
        )
        response = self._create(
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt_content}],
            output_config={"format": {"type": "json_schema", "schema": CREATIVE_PLAN_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise CloudAIError("The AI declined to generate the creative plan.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise CloudAIError("The AI returned an unreadable creative plan.")

    # ------------------------------------------------------------------
    # Motion-graphics ad: scene spec for packages/ai_engine/motion_ad.py
    # ------------------------------------------------------------------
    def design_motion_ad(self, words: List[Dict[str, Any]], thumbnails: List[str], prompt: str, aspect: str,
                         duration: float, current_spec: Optional[Dict[str, Any]] = None, edit_request: str = "") -> Dict[str, Any]:
        """Designs (or, with current_spec + edit_request, edits) the scenes of a voiceover-timed motion ad."""
        w, h = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}.get(aspect, (1080, 1920))
        content: List[Dict[str, Any]] = []
        for i, path in enumerate(thumbnails):
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({"type": "text", "text": f"Photo {i}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        timed = "\n".join(f"{x['start']:.2f}-{x['end']:.2f} {x['text']}" for x in words)
        text = (MOTION_AD_GUIDE.format(aspect=aspect, w=w, h=h)
                + f"\nVOICEOVER ({duration:.1f}s, word timestamps in seconds):\n{timed}\n"
                + f"\nPHOTOS: numbered 0-{len(thumbnails) - 1} above.\n"
                + f"\nEDITOR'S BRIEF: {prompt or 'A high-converting ad that explains what the voiceover says.'}\n")
        if current_spec is not None:
            text += ("\nCURRENT DESIGN (JSON):\n" + json.dumps(current_spec, ensure_ascii=False)
                     + f"\n\nEDIT REQUEST: {edit_request}\n"
                     + "Return the complete design with ONLY this change applied; keep every other scene and element as it is.")
        else:
            text += "\nDesign the complete ad from the first word to the last."
        content.append({"type": "text", "text": text})
        response = self._create(
            max_tokens=32000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": MOTION_AD_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            raise CloudAIError("The AI declined to design this ad.")
        if response.stop_reason == "max_tokens":
            raise CloudAIError("The ad design was too long. Try a shorter voiceover or a simpler brief.")
        out = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise CloudAIError("The AI returned an unreadable ad design.")

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
cloud_ai = cloud_ai_client

