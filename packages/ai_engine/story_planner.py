"""
AI Video Flow - 3-in-1 Storyboard & Timeline Planner
Constructs narrative, cinematic edit sequences for:
1. Wedding Teaser (30-60s, fast-paced reel with Hook + Chronological build)
2. Cinematic Highlight (3-5 mins, 3-Act Chronological structure, Wide/Close balanced, beat-aligned)
3. Full Wedding Film (15-60 mins, complete chronological ritual preservation)

Core Editorial Principle:
- Strict Chronological Progression: Matkor -> Haldi -> Baraat -> Varmala -> Pheras -> Bidaai.
  Rituals never scramble or jump across distant ceremonies.
- Local Micro-Variation: Within the same ceremony, Wide, Mid, and Close-Up shots
  are smoothly alternated to create professional cinematic rhythm without jump cuts.
"""

import math
import os
import re
from typing import List, Dict, Any, Optional


def parse_prompt_directives(instructions: str) -> Dict[str, Any]:
    """
    Parses user natural language editing prompts (Hindi, Hinglish, English) into
    concrete cinematic editorial rules: target duration, pacing, hero hook,
    priority rituals, slow-motion, color style, and aspect ratio.
    """
    if not instructions or not isinstance(instructions, str):
        return {"pace_factor": 1.0, "priorities": []}

    txt = instructions.lower()
    directives: Dict[str, Any] = {"pace_factor": 1.0, "priorities": []}

    # 1. Target duration in seconds or minutes (e.g. '30s', '45 sec', '3 min')
    dur_sec_match = re.search(r'(\d+)\s*(?:s|sec|seconds?|sekund)\b', txt)
    dur_min_match = re.search(r'(\d+)\s*(?:m|min|mins|minutes?|minat)\b', txt)
    if dur_sec_match:
        val = float(dur_sec_match.group(1))
        if 10.0 <= val <= 3600.0:
            directives["target_duration"] = val
    elif dur_min_match:
        val = float(dur_min_match.group(1)) * 60.0
        if 60.0 <= val <= 7200.0:
            directives["target_duration"] = val

    # 1.5. Detect reference video URL (YouTube, Vimeo, Instagram, etc.) or local file path in the prompt
    yt_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w\-]+|youtu\.be/[\w\-]+|youtube\.com/shorts/[\w\-]+))', instructions)
    url_match = re.search(r'(https?://[^\s\"\'<>]+)', instructions)
    if yt_match:
        cand_url = yt_match.group(1).strip()
        directives["ref_video"] = cand_url
        directives["ref_video_path"] = cand_url
        directives["ref_video_url"] = cand_url
    elif url_match:
        cand_url = url_match.group(1).strip()
        directives["ref_video"] = cand_url
        directives["ref_video_path"] = cand_url
        directives["ref_video_url"] = cand_url
    else:
        ref_match = re.search(r'([A-Za-z]:\\[^\n\r\"\'<>]+\.(?:mp4|mov|mxf|mkv|avi)|/[^\n\r\"\'<>\s]+\.(?:mp4|mov|mxf|mkv|avi)|\b[\w\-\.\/]+\.(?:mp4|mov|mxf|mkv|avi))', instructions, re.IGNORECASE)
        if ref_match:
            cand_path = ref_match.group(1).strip()
            directives["ref_video"] = cand_path
            directives["ref_video_path"] = cand_path

    # 2. Pacing (fast vs slow/cinematic)
    fast_words = ["fast", "snappy", "quick", "tez", "jaldi", "dynamic", "rapid", "energetic", "speed", "beat-cut", "tight"]
    slow_words = ["slow", "cinematic", "romantic", "dheere", "peaceful", "emotional", "dreamy", "calm", "relaxed", "poetic"]
    if any(w in txt for w in fast_words):
        directives["pacing"] = "fast"
        directives["pace_factor"] = 0.72
    elif any(w in txt for w in slow_words):
        directives["pacing"] = "slow"
        directives["pace_factor"] = 1.35
    else:
        directives["pacing"] = "normal"
        directives["pace_factor"] = 1.0

    # 3. Hook shot preference (Bride, Varmala, Groom, Couple, Drone)
    if any(w in txt for w in ["bride", "dulhan", "lehenga"]) and any(w in txt for w in ["first", "pehle", "start", "hook", "opening", "hero"]):
        directives["hook_preference"] = "bride"
    elif any(w in txt for w in ["varmala", "jaimala", "garland"]) and any(w in txt for w in ["first", "pehle", "start", "hook", "opening"]):
        directives["hook_preference"] = "varmala"
    elif any(w in txt for w in ["groom", "dulha", "swag", "car"]):
        directives["hook_preference"] = "groom"
    elif any(w in txt for w in ["couple", "romantic", "portrait", "glance"]):
        directives["hook_preference"] = "couple"
    elif any(w in txt for w in ["drone", "wide", "venue", "mandap"]):
        directives["hook_preference"] = "wide"

    # 4. Priority scene boosts
    priorities = []
    if any(w in txt for w in ["bride", "dulhan", "lehenga", "makeup"]):
        priorities.append("bride")
    if any(w in txt for w in ["groom", "dulha", "sehra", "swag"]):
        priorities.append("groom")
    if any(w in txt for w in ["varmala", "jaimala", "garland"]):
        priorities.append("varmala")
    if any(w in txt for w in ["phera", "pheras", "sindoor", "mangalsutra", "fire", "mandap"]):
        priorities.append("pheras")
    if any(w in txt for w in ["baraat", "barat", "dance", "dancing", "dhol", "bhangra"]):
        priorities.append("baraat")
    if any(w in txt for w in ["haldi", "yellow"]):
        priorities.append("haldi")
    if any(w in txt for w in ["mehndi", "mehendi", "henna"]):
        priorities.append("mehndi")
    if any(w in txt for w in ["bidaai", "vidai", "emotional", "crying"]):
        priorities.append("bidaai")
    directives["priorities"] = priorities

    # 5. Aspect ratio
    if any(w in txt for w in ["1:1", "square", "ration 1:1", "ratio 1:1", "feed ad", "feed post"]):
        directives["aspect_ratio"] = "1:1 (1080x1080)"
        directives["is_square"] = True
    elif any(w in txt for w in ["vertical", "9:16", "reel", "shorts", "tiktok"]):
        directives["aspect_ratio"] = "9:16 (1080x1920)"
        directives["is_vertical"] = True
    elif any(w in txt for w in ["letterbox", "2.35", "2.39", "cinemascope", "anamorphic", "bars"]):
        directives["aspect_ratio"] = "2.39:1 Cinemascope"
        directives["letterbox"] = True

    # 6. Color Grade style
    if any(w in txt for w in ["warm", "royal", "gold", "golden"]):
        directives["color_style"] = "Royal Warm"
    elif any(w in txt for w in ["vintage", "retro", "classic"]):
        directives["color_style"] = "Vintage Wedding"
    elif any(w in txt for w in ["moody", "cool", "neutral", "film"]):
        directives["color_style"] = "Neutral Wedding"

    # 7. Slow motion
    if any(w in txt for w in ["slow motion", "slowmo", "slow-mo", "50%"]):
        directives["slow_motion"] = True

    # 8. Audio Ducking
    if any(w in txt for w in ["duck", "auto duck", "auto-duck", "dialogue"]):
        directives["auto_ducking"] = True

    return directives


class StoryPlanner:
    """
    Assembles curated, trimmed video clips into cohesive narrative timelines.
    """

    def _select_chronological_sequence(
        self,
        clips: List[Dict[str, Any]],
        target_count: int,
        mix_framing: bool = True,
        exclude_paths: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Selects target_count unique clips strictly progressing along the shoot timeline.
        Strictly prevents clip duplication: every selected video clip is unique.
        Within each chronological slice, picks the highest-quality shot.
        Applies local micro-alternation (Wide / Mid / Close-up) within the same scene.
        """
        seen_paths = set(exclude_paths or [])
        available_pool = [c for c in clips if c.get("file_path") and c.get("file_path") not in seen_paths]
        total = len(available_pool)
        if total == 0:
            return []

        selected = []
        if total <= target_count:
            for c in available_pool:
                if c.get("file_path") not in seen_paths:
                    selected.append(dict(c))
                    seen_paths.add(c.get("file_path"))
        else:
            step = total / float(target_count)
            for i in range(target_count):
                start_idx = int(round(i * step))
                end_idx = min(total, int(round((i + 1) * step)))
                if start_idx >= end_idx:
                    end_idx = min(total, start_idx + 1)
                window = available_pool[start_idx:end_idx]
                candidates = [c for c in window if c.get("file_path") not in seen_paths]
                if not candidates:
                    candidates = [c for c in available_pool if c.get("file_path") not in seen_paths]
                if not candidates:
                    break
                best_clip = max(candidates, key=lambda c: c.get("quality_score", 0))
                selected.append(dict(best_clip))
                seen_paths.add(best_clip.get("file_path"))

        # Local Micro-Variation: Mix Wide, Mid, and Close-Up shots within the immediate scene
        # Only swaps adjacent clips if they belong to the same local neighborhood (<= 2 positions apart)
        if mix_framing and len(selected) > 3:
            for i in range(len(selected) - 2):
                t1 = selected[i].get("shot_type", "mid")
                t2 = selected[i + 1].get("shot_type", "mid")
                t3 = selected[i + 2].get("shot_type", "mid")
                # If two identical framings in a row (e.g. wide+wide or close+close) and third is different:
                if t1 == t2 and t2 != t3:
                    # Micro-swap i+1 and i+2 to break monotony and create cinematic rhythm
                    selected[i + 1], selected[i + 2] = selected[i + 2], selected[i + 1]

        return selected

    def plan_teaser(
        self,
        analyzed_clips: List[Dict[str, Any]],
        target_duration: float = 60.0,
        is_vertical: bool = False,
        instructions: str = "",
        ref_video_path: Optional[str] = None,
        beat_times: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Creates an ultra-dynamic teaser plan based on editorial directives, user prompts,
        and reference video rhythm:
        1. Starts with a Cinematic Hook (highest-scoring hero portrait / requested moment).
        2. Followed by a rapid, chronological sprint through the wedding rituals in proper order.
        3. Strict No-Duplicate rule: each video clip is used at most once.
        4. Slow-Mo & Fast Speed Ramping: 50% slow-motion on glamour/portraits, 130% on dance energy.
        5. Golden Action Trimming: Slices away idle pre-roll and dead post-roll, keeping only peak action.
        6. Locked to Reels/Music Audio Beats.
        """
        if not analyzed_clips:
            return {"format": "Teaser_Reel_60s", "clips": [], "total_duration": 0.0}

        directives = parse_prompt_directives(instructions)
        if not ref_video_path and directives.get("ref_video"):
            ref_video_path = directives["ref_video"]

        ref_pacing = self.analyze_reference_video_pace(ref_video_path)
        has_ref = ref_pacing.get("has_ref", False)

        if directives.get("target_duration"):
            target_duration = directives["target_duration"]
        if directives.get("is_vertical") or ref_pacing.get("is_vertical"):
            is_vertical = True

        pace_factor = directives.get("pace_factor", 1.0)
        if has_ref:
            avg_dur = max(0.9, min(2.5, ref_pacing.get("target_shot_dur", 1.5)))
        else:
            avg_dur = max(0.9, min(2.5, 1.5 * pace_factor))
        needed_clips = max(8, int(math.ceil(target_duration / avg_dur)))

        def matches_pref(c: Dict[str, Any], kw: str) -> bool:
            f = (c.get("file_path") or "").lower()
            m = (c.get("ai_moment") or "").lower()
            return kw in f or kw in m

        # Find best hero Hook shot (incorporating prompt hook preference)
        hook_pref = directives.get("hook_preference")
        hook_clip = None

        if hook_pref:
            kw_map = {
                "bride": ["bride", "dulhan", "lehenga", "portrait", "close_up"],
                "varmala": ["varmala", "jaimala", "garland", "stage"],
                "groom": ["groom", "dulha", "sehra", "swag"],
                "couple": ["couple", "romantic", "glance", "together"],
                "wide": ["drone", "wide", "venue", "mandap"]
            }
            kws = kw_map.get(hook_pref, [hook_pref])
            matching = [c for c in analyzed_clips if any(matches_pref(c, k) for k in kws)]
            if matching:
                matching.sort(key=lambda c: c.get("quality_score", 0), reverse=True)
                hook_clip = matching[0]

        if not hook_clip:
            candidates = sorted(analyzed_clips, key=lambda c: (c.get("shot_type") == "close_up", c.get("quality_score", 0)), reverse=True)
            hook_clip = candidates[0] if candidates else analyzed_clips[0]

        # Chronological progression for the rest of the teaser with priority weighting and STRICT NO-DUPLICATES
        used_paths = {hook_clip.get("file_path")} if hook_clip and hook_clip.get("file_path") else set()
        remaining_count = max(1, needed_clips - 1)
        priorities = directives.get("priorities", [])
        chronological_pool = []
        for c in analyzed_clips:
            if c.get("file_path") in used_paths:
                continue
            cp = dict(c)
            # Boost priority moments requested in prompt
            if priorities and any(matches_pref(cp, p) for p in priorities):
                cp["quality_score"] = cp.get("quality_score", 0) + 30.0
            chronological_pool.append(cp)

        body_clips = self._select_chronological_sequence(chronological_pool, remaining_count, mix_framing=True, exclude_paths=used_paths)

        selected = [hook_clip]
        for bc in body_clips:
            if bc.get("file_path") not in used_paths:
                selected.append(bc)
                used_paths.add(bc.get("file_path"))

        timeline_entries = []
        curr_time = 0.0

        for idx, clip in enumerate(selected):
            # Hook shot gets 2.0s * pace, rest mirror reference reel pattern or snappy cuts
            if idx == 0:
                cut_dur = max(1.4, round(2.0 * pace_factor, 2))
                role = f"Cinematic Hook ({hook_pref.capitalize() if hook_pref else 'Hero Portrait'})"
            elif has_ref and ref_pacing.get("shot_pattern"):
                pat = ref_pacing["shot_pattern"]
                ref_dur = pat[(idx - 1) % len(pat)]
                cut_dur = max(0.8, min(3.2, round(ref_dur, 2)))
                role = "Reference Rhythmic Beat Cut"
            else:
                base = 1.3 if idx % 2 == 0 else 1.7
                cut_dur = max(0.9, round(base * pace_factor, 2))
                role = "Chronological Beat Cut"

            # Reels / Audio Beat Sync: snap cut ending to music beat transient
            if beat_times:
                target_t = curr_time + cut_dur
                candidates = [b for b in beat_times if b >= curr_time + 0.75 and abs(b - target_t) < 0.55]
                if candidates:
                    closest_beat = min(candidates, key=lambda b: abs(b - target_t))
                    cut_dur = max(0.75, round(closest_beat - curr_time, 2))

            # Dynamic Retiming: Slow-Motion (50% speed) & Fast Snappy (130% speed)
            fpath_l = (clip.get("file_path") or "").lower()
            moment_l = (clip.get("ai_moment") or "").lower()
            native_fps = float(clip.get("native_fps", 25.0))
            shot_type = clip.get("shot_type", "mid")

            is_slow = (
                native_fps >= 48.0 or
                idx == 0 or
                any(w in fpath_l or w in moment_l for w in [
                    "bride", "dulhan", "lehenga", "portrait", "varmala", "jaimala",
                    "garland", "glance", "romantic", "couple", "phera", "sindoor",
                    "bidaai", "vidai", "slow"
                ]) or
                (shot_type == "close_up" and idx % 2 == 1)
            )
            is_fast = (
                not is_slow and (
                    any(w in fpath_l or w in moment_l for w in [
                        "dance", "dancing", "baraat", "barat", "dhol", "bhangra",
                        "energy", "party", "fast", "swag", "walk"
                    ]) or (idx % 3 == 0 and cut_dur < 1.3)
                )
            )

            is_still = bool(clip.get("is_still"))
            if is_still:
                speed_factor = 1.0
                speed_label = "Motion Graphics / Ken Burns Pan-Zoom"
                target_in = 0.0
                target_out = cut_dur
                action_peak = round(cut_dur * 0.5, 2)
                role = "Motion Graphics / PSD Layer" if clip.get("is_psd") else "Graphic Overlay / Slide"
            else:
                if is_slow:
                    speed_factor = 0.5  # 50% Slow Motion
                    speed_label = "50% Slow-Mo (Optical Flow)"
                elif is_fast:
                    speed_factor = 1.3  # 130% Snappy Speed Ramp
                    speed_label = "130% Snappy Beat Ramp"
                else:
                    speed_factor = 1.0
                    speed_label = "Normal Speed"

                # Golden Action Trim: slices away idle pre-roll and dead post-roll
                clip_dur = float(clip.get("duration", 0.0))
                action_start = float(clip.get("action_start_seconds", clip.get("in_seconds", 0.0)))
                action_end = float(clip.get("action_end_seconds", clip.get("out_seconds", clip_dur)))
                action_peak = float(clip.get("action_peak_seconds", action_start + (action_end - action_start) * 0.45))
                if action_peak <= 0:
                    action_peak = action_start + (cut_dur * speed_factor * 0.5)

                source_needed = round(cut_dur * speed_factor, 2)
                if (action_end - action_start) >= source_needed:
                    target_in = max(action_start, action_peak - (source_needed * 0.35))
                    target_out = target_in + source_needed
                    if target_out > action_end:
                        target_out = min(clip_dur, action_end)
                        target_in = max(action_start, target_out - source_needed)
                else:
                    target_in = max(0.0, action_start)
                    target_out = min(clip_dur, target_in + source_needed)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(target_in * native_fps),
                "out_frame": int(target_out * native_fps),
                "duration": cut_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "role": role,
                "speed": speed_factor,
                "speed_percent": int(speed_factor * 100),
                "speed_label": speed_label,
                "retime_process": "Optical Flow" if speed_factor != 1.0 else "Normal",
                "action_trimmed": True,
                "action_peak_seconds": round(action_peak, 2)
            })
            curr_time += cut_dur
            if curr_time >= target_duration:
                break

        seq_name = f"01_Wedding_Teaser_{int(target_duration)}s" if target_duration != 60.0 else "01_Wedding_Teaser_60s"
        return {
            "format": f"Teaser_Reel_{int(target_duration)}s",
            "aspect_ratio": "9:16 (1080x1920)" if is_vertical else "16:9 (1920x1080 / 3840x2160)",
            "total_duration": round(curr_time, 2),
            "clip_count": len(timeline_entries),
            "sequence_name": seq_name,
            "clips": timeline_entries,
            "prompt_directives": directives
        }

    def analyze_reference_video_pace(self, ref_video_path: Optional[str]) -> Dict[str, Any]:
        """
        Analyzes reference video/reel to extract shot pacing, average shot duration (ASD),
        aspect ratio (9:16 vertical vs 16:9 vs 2.39:1), and cut rhythm.
        """
        if not ref_video_path or not os.path.isfile(ref_video_path):
            return {"has_ref": False, "target_shot_dur": 2.4, "pacing": "balanced", "aspect_ratio": "16:9"}

        try:
            import cv2
            import numpy as np
            cap = cv2.VideoCapture(ref_video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080

            is_vertical = height > width * 1.15
            is_letterbox = (width / max(1, height)) >= 2.2
            if is_vertical:
                aspect_ratio = "9:16 (1080x1920)"
            elif is_letterbox:
                aspect_ratio = "2.39:1 Cinemascope"
            else:
                aspect_ratio = "16:9 Cinematic"

            # Sample every 5 frames to detect scene cuts
            step = 5
            prev_gray = None
            cuts = [0.0]
            curr_f = 0

            # Scan up to 120 seconds of reference
            max_scan_frames = min(total_frames, int(fps * 120))
            while curr_f < max_scan_frames:
                cap.set(cv2.CAP_PROP_POS_FRAMES, curr_f)
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                small = cv2.resize(frame, (160, 90))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    diff = cv2.absdiff(prev_gray, gray)
                    mean_diff = float(np.mean(diff))
                    if mean_diff > 35.0:
                        t = round(curr_f / fps, 2)
                        if t - cuts[-1] >= 0.8:
                            cuts.append(t)
                prev_gray = gray
                curr_f += step

            cap.release()

            if len(cuts) > 2:
                shot_durs = [round(cuts[i] - cuts[i - 1], 2) for i in range(1, len(cuts))]
                shot_pattern = [max(0.8, min(4.5, d)) for d in shot_durs]
                avg_shot_dur = float(np.median(shot_pattern))
                avg_shot_dur = max(1.1, min(4.2, avg_shot_dur))
                return {
                    "has_ref": True,
                    "target_shot_dur": round(avg_shot_dur, 2),
                    "shot_pattern": shot_pattern,
                    "cut_count": len(cuts),
                    "pacing": "fast" if avg_shot_dur < 1.8 else ("slow" if avg_shot_dur > 2.8 else "balanced"),
                    "is_vertical": is_vertical,
                    "is_letterbox": is_letterbox,
                    "aspect_ratio": aspect_ratio,
                    "ref_path": ref_video_path
                }
            return {
                "has_ref": True,
                "target_shot_dur": 2.2,
                "shot_pattern": [2.2, 1.8, 2.5, 1.5, 2.0],
                "cut_count": len(cuts),
                "pacing": "balanced",
                "is_vertical": is_vertical,
                "is_letterbox": is_letterbox,
                "aspect_ratio": aspect_ratio,
                "ref_path": ref_video_path
            }
        except Exception:
            pass

        return {"has_ref": False, "target_shot_dur": 2.4, "pacing": "balanced", "aspect_ratio": "16:9"}

    def plan_highlight(
        self,
        analyzed_clips: List[Dict[str, Any]],
        beat_times: Optional[List[float]] = None,
        target_duration: float = 240.0,
        ref_video_path: Optional[str] = None,
        instructions: str = ""
    ) -> Dict[str, Any]:
        """
        Creates a Masterclass 4-Minute Wedding Film with High-End Storytelling Architecture:
        1. Opening Hook: Grand Ultra-Wide Establishing Shot (Venue / Architecture / Landscape / Drone).
        2. Couple & Portraits Showcase:
           - Bride Solo Glamour & Portraits (Jewelry, smile, makeup, lehenga).
           - Groom Solo Swag & Arrival (Turban, watch, sunglasses, portrait).
           - Romantic Couple Glance / First Look / Hero Portrait.
        3. Joyous Functions & Rituals in Flow (Haldi / Mehndi / Tilak / Baraat / Dancing) beat-synced.
        4. Grand Sacred Climax (Varmala / Fire Pheras / Sindoor).
        5. Grand Romantic Finale: Couple sunset/evening portrait & tender Bidaai.
        6. 100% Reference Video Pace Matching if reference video is provided.
        7. Dynamically reshaped by user prompt (pacing, target duration, hero scenes, aspect ratio).
        """
        if not analyzed_clips:
            return {"format": "Cinematic_Highlight_4min", "clips": [], "total_duration": 0.0}

        directives = parse_prompt_directives(instructions)
        if not ref_video_path and directives.get("ref_video"):
            ref_video_path = directives["ref_video"]
        if directives.get("target_duration"):
            target_duration = directives["target_duration"]

        pace_factor = directives.get("pace_factor", 1.0)
        priorities = directives.get("priorities", [])

        # Analyze reference video rhythm if provided
        ref_pacing = self.analyze_reference_video_pace(ref_video_path)
        ref_shot_dur = ref_pacing.get("target_shot_dur", 2.4)
        has_ref = ref_pacing.get("has_ref", False)

        def matches_pref(c: Dict[str, Any], kw: str) -> bool:
            f = (c.get("file_path") or "").lower()
            m = (c.get("ai_moment") or "").lower()
            return kw in f or kw in m

        used_paths = set()
        timeline_entries = []
        curr_time = 0.0
        entry_idx = 0

        def snap_duration(dur: float, current_t: float) -> float:
            nonlocal entry_idx
            if has_ref and ref_pacing.get("shot_pattern"):
                pat = ref_pacing["shot_pattern"]
                dur = max(0.9, min(4.5, pat[entry_idx % len(pat)]))
                entry_idx += 1
            elif has_ref:
                dur = ref_shot_dur
            else:
                dur = max(1.2, round(dur * pace_factor, 2))
            if not beat_times:
                return dur
            target_t = current_t + dur
            candidates = [b for b in beat_times if b >= current_t + 1.2 and abs(b - target_t) < 0.65]
            if candidates:
                closest_beat = min(candidates, key=lambda b: abs(b - target_t))
                return max(1.2, round(closest_beat - current_t, 2))
            return dur

        def add_entry(clip: Dict[str, Any], cut_dur: float, role: str, act_name: str) -> bool:
            nonlocal curr_time
            fpath = clip.get("file_path", "")
            if not fpath or fpath in used_paths:
                return False
            actual_dur = snap_duration(cut_dur, curr_time)

            fpath_l = fpath.lower()
            moment_l = (clip.get("ai_moment") or "").lower()
            fps = float(clip.get("native_fps", 25.0))
            shot_type = clip.get("shot_type", "mid")

            # Retiming: Slow-Motion (50% speed) & Fast (130% speed)
            is_slow = (
                fps >= 48.0 or
                any(w in fpath_l or w in moment_l for w in [
                    "bride", "dulhan", "lehenga", "portrait", "varmala", "jaimala",
                    "garland", "glance", "romantic", "couple", "phera", "sindoor",
                    "bidaai", "vidai", "slow"
                ]) or
                (shot_type == "close_up" and "act 1" in act_name.lower())
            )
            is_fast = (
                not is_slow and any(w in fpath_l or w in moment_l for w in [
                    "dance", "dancing", "baraat", "barat", "dhol", "bhangra", "energy", "party"
                ])
            )

            is_still = bool(clip.get("is_still"))
            if is_still:
                speed_factor = 1.0
                speed_label = "Motion Graphics / Ken Burns Pan-Zoom"
                target_in = 0.0
                target_out = actual_dur
                action_peak = round(actual_dur * 0.5, 2)
                if clip.get("is_psd"):
                    role = "Motion Graphics / PSD Layer"
                else:
                    role = "Motion Graphics / Still Slide"
            else:
                if is_slow:
                    speed_factor = 0.5
                    speed_label = "50% Slow-Mo (Optical Flow)"
                elif is_fast:
                    speed_factor = 1.3
                    speed_label = "130% Snappy Beat Ramp"
                else:
                    speed_factor = 1.0
                    speed_label = "Normal Speed"

                clip_dur = float(clip.get("duration", 0.0))
                action_start = float(clip.get("action_start_seconds", clip.get("in_seconds", 0.0)))
                action_end = float(clip.get("action_end_seconds", clip.get("out_seconds", clip_dur)))
                action_peak = float(clip.get("action_peak_seconds", action_start + (action_end - action_start) * 0.45))
                if action_peak <= 0:
                    action_peak = action_start + (actual_dur * speed_factor * 0.5)

                source_needed = round(actual_dur * speed_factor, 2)
                if (action_end - action_start) >= source_needed:
                    target_in = max(action_start, action_peak - (source_needed * 0.35))
                    target_out = target_in + source_needed
                    if target_out > action_end:
                        target_out = min(clip_dur, action_end)
                        target_in = max(action_start, target_out - source_needed)
                else:
                    target_in = max(0.0, action_start)
                    target_out = min(clip_dur, target_in + source_needed)

            timeline_entries.append({
                "clip_name": os.path.basename(fpath),
                "file_path": fpath,
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + actual_dur, 2),
                "in_frame": int(target_in * fps),
                "out_frame": int(target_out * fps),
                "duration": actual_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "role": role,
                "act": act_name,
                "speed": speed_factor,
                "speed_percent": int(speed_factor * 100),
                "speed_label": speed_label,
                "retime_process": "Optical Flow" if speed_factor != 1.0 else "Normal",
                "action_trimmed": True,
                "action_peak_seconds": round(action_peak, 2)
            })
            curr_time += actual_dur
            used_paths.add(fpath)
            return True

        # =================================================================
        # PHASE 1: Grand Ultra-Wide Establishing Hook (Venue / Aerial / Mandap)
        # =================================================================
        wide_candidates = [c for c in analyzed_clips if c.get("shot_type") == "wide"]
        wide_candidates.sort(key=lambda c: c.get("quality_score", 0), reverse=True)
        if wide_candidates:
            hero_wide = wide_candidates[0]
            add_entry(hero_wide, 3.4, "Grand Ultra-Wide Establishing Shot (Venue/Drone)", "Act 1: Opening & Grand Venue Hook")
        elif analyzed_clips:
            best_first = max(analyzed_clips[:5], key=lambda c: c.get("quality_score", 0))
            add_entry(best_first, 3.4, "Grand Establishing Hook", "Act 1: Opening & Grand Venue Hook")

        # =================================================================
        # PHASE 2: Bride & Groom Solo Glamour & Hero Portraits
        # =================================================================
        # Bride candidates: early portion of shoot, close_up / portrait
        early_pool = analyzed_clips[:max(4, int(len(analyzed_clips) * 0.40))]
        bride_candidates = [c for c in early_pool if c.get("shot_type") in ("close_up", "mid") and c.get("file_path") not in used_paths]
        bride_candidates.sort(key=lambda c: (
            any(matches_pref(c, "bride") for _ in [1]) if "bride" in priorities else False,
            c.get("shot_type") == "close_up",
            c.get("quality_score", 0)
        ), reverse=True)
        for b_clip in bride_candidates[:2]:
            add_entry(b_clip, 2.8, "Bride Solo Glamour & Jewelry Portrait", "Act 1: Bride & Groom Portraits")

        # Groom candidates: mid shots, sharp swagger portraits
        groom_candidates = [c for c in early_pool if c.get("shot_type") in ("mid", "close_up") and c.get("file_path") not in used_paths]
        groom_candidates.sort(key=lambda c: (
            any(matches_pref(c, "groom") for _ in [1]) if "groom" in priorities else False,
            c.get("quality_score", 0)
        ), reverse=True)
        for g_clip in groom_candidates[:2]:
            add_entry(g_clip, 2.6, "Groom Swag & Preparation Portrait", "Act 1: Bride & Groom Portraits")

        # Romantic Couple Hook / First Glance
        couple_candidates = [c for c in analyzed_clips if c.get("file_path") not in used_paths]
        couple_candidates.sort(key=lambda c: (c.get("shot_type") in ("mid", "close_up"), c.get("quality_score", 0)), reverse=True)
        if couple_candidates:
            add_entry(couple_candidates[0], 3.0, "Hero Couple Romantic Glance / First Look", "Act 1: Couple Romantic Hook")

        # =================================================================
        # PHASE 3: Celebrations & Functions (Haldi / Mehndi / Baraat / Dancing)
        # =================================================================
        total_clips = len(analyzed_clips)
        idx_act1_end = max(1, int(total_clips * 0.25))
        idx_act2_end = max(idx_act1_end + 1, int(total_clips * 0.70))

        functions_pool = [c for c in analyzed_clips[idx_act1_end:idx_act2_end] if c.get("file_path") not in used_paths]
        needed_func_clips = max(8, int(round((target_duration * 0.45) / max(1.2, 2.3 * pace_factor))))
        functions_selected = self._select_chronological_sequence(functions_pool, needed_func_clips, mix_framing=True)

        for f_clip in functions_selected:
            if curr_time >= target_duration * 0.75:
                break
            add_entry(f_clip, 2.2, "Joyous Ceremony & Celebration (Haldi/Baraat)", "Act 2: Core Ceremonies & Energy")

        # =================================================================
        # PHASE 4: Sacred Climax (Varmala / Fire Pheras / Sindoor)
        # =================================================================
        climax_pool = [c for c in analyzed_clips[idx_act2_end:] if c.get("file_path") not in used_paths]
        needed_climax_clips = max(6, int(round((target_duration * 0.20) / max(1.0, 1.8 * pace_factor))))
        climax_selected = self._select_chronological_sequence(climax_pool, needed_climax_clips, mix_framing=True)

        for c_clip in climax_selected:
            if curr_time >= target_duration - 12.0:
                break
            add_entry(c_clip, 1.8, "Grand Wedding Climax (Varmala/Pheras)", "Act 3: Grand Climax & Sacred Vows")

        # =================================================================
        # PHASE 5: Grand Romantic Finale (Sunset Couple Frame & Bidaai)
        # =================================================================
        ending_candidates = [c for c in analyzed_clips[idx_act2_end:] if c.get("file_path") not in used_paths]
        ending_candidates.sort(key=lambda c: (c.get("shot_type") in ("close_up", "mid"), c.get("quality_score", 0)), reverse=True)
        if ending_candidates:
            add_entry(ending_candidates[0], 3.2, "Grand Romantic Couple Finale / Sunset Glances", "Act 4: Emotional & Couple Finale")
        if len(ending_candidates) > 1 and curr_time < target_duration:
            add_entry(ending_candidates[1], 3.5, "Tender Bidaai & Couple Farewell", "Act 4: Emotional & Couple Finale")

        dur_mins = round(target_duration / 60.0, 1)
        dur_label = f"{int(dur_mins)}min" if dur_mins == int(dur_mins) else f"{dur_mins}min"
        seq_name = f"02_Cinematic_Highlight_{dur_label}" if target_duration != 240.0 else "02_Cinematic_Highlight_4min"
        aspect_ratio = directives.get("aspect_ratio") or ("16:9 Cinematic")

        return {
            "format": f"Cinematic_Highlight_{dur_label}",
            "aspect_ratio": aspect_ratio,
            "total_duration": round(curr_time, 2),
            "clip_count": len(timeline_entries),
            "sequence_name": seq_name,
            "clips": timeline_entries,
            "reference_matched": has_ref,
            "target_shot_pace": ref_shot_dur,
            "prompt_directives": directives
        }

    def plan_full_film(self, analyzed_clips: List[Dict[str, Any]], instructions: str = "") -> Dict[str, Any]:
        """
        Chronological complete wedding film preserving 100% of original event order.
        Conservative / Gentle Trimming: Preserves 85% to 100% of each clip duration!
        Only trims genuine camera jerk at button press / shutdown.
        """
        directives = parse_prompt_directives(instructions)
        tighten = directives.get("pacing") == "fast"
        curr_time = 0.0
        timeline_entries = []

        for clip in analyzed_clips:
            is_still = bool(clip.get("is_still"))
            if is_still:
                dur = 4.0
                in_sec = 0.0
                out_sec = dur
                fps = clip.get("native_fps", 25.0)
                in_frame = 0
                out_frame = int(dur * fps)
                role = "Motion Graphics / PSD Layer" if clip.get("is_psd") else "Graphic Still"
            else:
                # Prefer conservative full film cut points (preserving action, skipping idle pre-roll)
                in_sec = clip.get("full_in_seconds", clip.get("action_start_seconds", clip.get("in_seconds", 0.0)))
                out_sec = clip.get("full_out_seconds", clip.get("action_end_seconds", clip.get("out_seconds", clip.get("duration", 0.0))))
                dur = max(1.0, round(out_sec - in_sec, 2))
                if tighten and dur > 4.0:
                    dur = round(dur * 0.82, 2)
                    out_sec = in_sec + dur

                fps = clip.get("native_fps", 25.0)
                in_frame = int(clip.get("full_in_frame", clip.get("action_start_frame", in_sec * fps)))
                out_frame = int(clip.get("full_out_frame", clip.get("action_end_frame", out_sec * fps)))
                role = "Chronological Full Film Event"

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + dur, 2),
                "in_frame": in_frame,
                "out_frame": out_frame,
                "duration": dur,
                "shot_type": clip.get("shot_type", "mid"),
                "role": "Chronological Full Film Event"
            })
            curr_time += dur

        return {
            "format": "Full_Wedding_Film",
            "aspect_ratio": "16:9",
            "total_duration": round(curr_time, 2),
            "total_minutes": round(curr_time / 60.0, 1),
            "clip_count": len(timeline_entries),
            "sequence_name": "03_Full_Wedding_Film",
            "clips": timeline_entries,
            "prompt_directives": directives
        }

    def _detect_voice_pauses(self, audio_path: str, target_count: int, max_dur: float) -> List[float]:
        try:
            import soundfile as sf
            import numpy as np
            data, sr = sf.read(audio_path)
            if data.ndim > 1:
                mono = np.mean(data, axis=1)
            else:
                mono = data
            tot_sec = min(float(len(mono) / sr), max_dur)
            hop = int(sr * 0.05)  # 50ms frames
            n_frames = int(tot_sec * sr) // hop
            if n_frames < 20:
                return []
            energies = np.array([np.sqrt(np.mean(mono[i*hop:(i+1)*hop]**2) + 1e-9) for i in range(n_frames)])
            max_e = np.max(energies)
            if max_e <= 0:
                return []
            db = 20 * np.log10(energies / max_e)
            is_pause = db < -24.0

            pauses = []
            start = None
            for i, p in enumerate(is_pause):
                if p and start is None:
                    start = i
                elif not p and start is not None:
                    if (i - start) >= 3:  # >= 150ms pause
                        mid = (start + i) / 2 * 0.05
                        if 1.2 < mid < tot_sec - 1.2:
                            pauses.append(mid)
                    start = None
            if not pauses:
                return []
            ideal = [i * (tot_sec / target_count) for i in range(1, target_count)]
            picked = []
            avail = list(pauses)
            for target_t in ideal:
                if not avail:
                    break
                closest = min(avail, key=lambda p: abs(p - target_t))
                picked.append(closest)
                avail.remove(closest)
            picked.sort()
            return [0.0] + [round(p, 3) for p in picked] + [round(tot_sec, 3)]
        except Exception:
            return []

    def plan_voiceover_motion_graphics(
        self,
        analyzed_clips: List[Dict[str, Any]],
        audio_path: Optional[str] = None,
        instructions: str = "",
        ref_video_path: Optional[str] = None,
        is_vertical: bool = False,
        target_duration: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Creates a custom motion-graphics timeline timed precisely to the user's voiceover/audio recording.
        Detects sentence pauses and phrase breaks in the audio so photos/PSD/PNG layers cut naturally
        with the speaker's voice, animated with alternating Ken Burns pan/zoom moves.
        """
        directives = parse_prompt_directives(instructions)
        is_square = directives.get("is_square") or directives.get("aspect_ratio") == "1:1 (1080x1080)"

        aspect_ratio = "1:1 (1080x1080)" if is_square else ("9:16 (1080x1920)" if is_vertical else "16:9")
        tl_w, tl_h = (1080, 1080) if is_square else ((1080, 1920) if is_vertical else (1920, 1080))

        # Determine audio duration
        audio_duration = 0.0
        if audio_path and os.path.isfile(audio_path):
            try:
                import soundfile as sf
                info = sf.info(audio_path)
                audio_duration = float(info.duration)
            except Exception:
                pass

        if target_duration and target_duration > 0 and (audio_duration == 0 or target_duration < audio_duration):
            total_duration = target_duration
        else:
            total_duration = audio_duration if audio_duration > 0 else (len(analyzed_clips) * 4.0)

        # Detect speech breaks in voiceover
        num_clips = max(1, len(analyzed_clips))
        cuts = []
        if audio_path and os.path.isfile(audio_path):
            cuts = self._detect_voice_pauses(audio_path, num_clips, total_duration)

        if not cuts or len(cuts) < 2:
            seg_len = total_duration / num_clips
            cuts = [round(i * seg_len, 3) for i in range(num_clips + 1)]

        KEN_BURNS_MOVES = [
            "Ken Burns Slow Push-In (1.0x -> 1.15x)",
            "Ken Burns Pan Left-to-Right",
            "Ken Burns Dynamic Pull-Out (1.15x -> 1.0x)",
            "Ken Burns Pan Right-to-Left",
            "Ken Burns Center Dynamic Pop",
            "Ken Burns Diagonal Drift (Up-Right)",
            "Ken Burns Subtle Slow Zoom",
        ]

        timeline_entries = []
        for i in range(min(num_clips, len(cuts) - 1)):
            c = analyzed_clips[i % len(analyzed_clips)]
            t_start = cuts[i]
            t_end = cuts[i + 1]
            seg_dur = max(0.5, t_end - t_start)
            fps = float(c.get("native_fps") or 25.0)
            move = KEN_BURNS_MOVES[i % len(KEN_BURNS_MOVES)]

            entry = {
                "clip_name": f"[{c.get('shot_type', 'graphic').upper()}] {os.path.basename(c.get('file_path', ''))}",
                "file_path": c.get("file_path"),
                "timeline_start": round(t_start, 3),
                "timeline_end": round(t_end, 3),
                "in_frame": 0,
                "out_frame": int(seg_dur * fps),
                "duration": round(seg_dur, 3),
                "shot_type": "graphic",
                "role": c.get("role") or f"Voiceover Scene {i+1}",
                "text_hook": c.get("text_hook", ""),
                "motion_effect": move,
                "speed": 1.0,
                "speed_percent": 100,
                "speed_label": f"Motion Graphics / {move}",
                "retime_process": "Normal",
                "action_trimmed": False,
                "action_peak_seconds": round(seg_dur / 2.0, 2)
            }
            timeline_entries.append(entry)

        seq_name = f"01_Voiceover_Motion_Graphics_{'1x1' if is_square else ('9x16' if is_vertical else '16x9')}"
        return {
            "sequence_name": seq_name,
            "aspect_ratio": aspect_ratio,
            "timeline_width": tl_w,
            "timeline_height": tl_h,
            "total_duration": round(total_duration, 2),
            "total_minutes": round(total_duration / 60.0, 2),
            "clip_count": len(timeline_entries),
            "clips": timeline_entries,
            "audio": audio_path,
            "audio_duration": audio_duration,
            "prompt_directives": directives
        }


# Global instance
story_planner = StoryPlanner()

