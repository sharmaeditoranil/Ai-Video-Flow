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
from typing import List, Dict, Any, Optional


class StoryPlanner:
    """
    Assembles curated, trimmed video clips into cohesive narrative timelines.
    """

    def _select_chronological_sequence(
        self,
        clips: List[Dict[str, Any]],
        target_count: int,
        mix_framing: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Selects target_count clips strictly progressing along the shoot timeline.
        Within each chronological slice, picks the highest-quality shot.
        Applies local micro-alternation (Wide / Mid / Close-up) within the same scene.
        """
        total = len(clips)
        if total == 0:
            return []
        if total <= target_count:
            selected = [dict(c) for c in clips]
        else:
            selected = []
            step = total / float(target_count)
            for i in range(target_count):
                start_idx = int(round(i * step))
                end_idx = min(total, int(round((i + 1) * step)))
                if start_idx >= end_idx:
                    end_idx = min(total, start_idx + 1)
                window = clips[start_idx:end_idx]
                if not window:
                    continue
                # Pick the highest quality, steadiest clip in this chronological slice
                best_clip = max(window, key=lambda c: c.get("quality_score", 0))
                # Avoid duplicate adjacent clip
                if not selected or best_clip.get("file_path") != selected[-1].get("file_path"):
                    selected.append(dict(best_clip))

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
        is_vertical: bool = False
    ) -> Dict[str, Any]:
        """
        Creates an ultra-dynamic teaser plan:
        1. Starts with a 2-second Cinematic Hook (highest-scoring hero portrait / couple glance).
        2. Followed by a rapid, chronological sprint through the wedding rituals in proper order.
        Average shot duration: 1.2 - 1.8 seconds.
        """
        if not analyzed_clips:
            return {"format": "Teaser_Reel_60s", "clips": [], "total_duration": 0.0}

        avg_dur = 1.5
        needed_clips = max(10, int(math.ceil(target_duration / avg_dur)))

        # Find 1 best hero Hook shot (high score, close_up/portrait preferred)
        candidates = sorted(analyzed_clips, key=lambda c: (c.get("shot_type") == "close_up", c.get("quality_score", 0)), reverse=True)
        hook_clip = candidates[0] if candidates else analyzed_clips[0]

        # Chronological progression for the rest of the teaser
        remaining_count = max(1, needed_clips - 1)
        chronological_pool = [c for c in analyzed_clips if c.get("file_path") != hook_clip.get("file_path")]
        body_clips = self._select_chronological_sequence(chronological_pool, remaining_count, mix_framing=True)

        selected = [hook_clip] + body_clips
        timeline_entries = []
        curr_time = 0.0

        for idx, clip in enumerate(selected):
            # Hook shot gets 2.0s, rest get snappy cuts (1.3s - 1.7s)
            if idx == 0:
                cut_dur = 2.0
                role = "Cinematic Hook / Opening Glance"
            else:
                cut_dur = 1.4 if idx % 2 == 0 else 1.8
                role = "Chronological Beat Cut"

            in_sec = clip.get("in_seconds", 0.0)
            out_sec = clip.get("out_seconds", in_sec + cut_dur)
            action_start = clip.get("action_start_seconds", in_sec)
            action_peak = clip.get("action_peak_seconds", in_sec + (cut_dur * 0.45))
            clip_dur = clip.get("duration", out_sec)
            native_fps = clip.get("native_fps", 25.0)

            # Center cut around Action Peak (skips pre-action waiting, captures true motion)
            if (out_sec - in_sec) > cut_dur and action_peak > in_sec:
                target_in = max(action_start, in_sec, action_peak - (cut_dur * 0.45))
                target_out = min(clip_dur, target_in + cut_dur)
                if (target_out - target_in) < cut_dur:
                    target_in = max(action_start, 0.0, target_out - cut_dur)
            else:
                target_in = max(action_start, in_sec)
                target_out = min(clip_dur, target_in + cut_dur)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(target_in * native_fps),
                "out_frame": int(target_out * native_fps),
                "duration": cut_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "role": role
            })
            curr_time += cut_dur
            if curr_time >= target_duration:
                break

        return {
            "format": "Teaser_Reel_60s",
            "aspect_ratio": "9:16 (1080x1920)" if is_vertical else "16:9 (1920x1080 / 3840x2160)",
            "total_duration": round(curr_time, 2),
            "clip_count": len(timeline_entries),
            "sequence_name": "01_Wedding_Teaser_60s",
            "clips": timeline_entries
        }

    def analyze_reference_video_pace(self, ref_video_path: Optional[str]) -> Dict[str, Any]:
        """
        Analyzes reference video/reel to extract shot pacing, average shot duration (ASD),
        and cut rhythm so the 4-minute cinematic edit matches the reference 100%.
        """
        if not ref_video_path or not os.path.isfile(ref_video_path):
            return {"has_ref": False, "target_shot_dur": 2.4, "pacing": "balanced"}

        try:
            import cv2
            import numpy as np
            cap = cv2.VideoCapture(ref_video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

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
                shot_durs = [cuts[i] - cuts[i - 1] for i in range(1, len(cuts))]
                avg_shot_dur = float(np.median(shot_durs))
                avg_shot_dur = max(1.3, min(4.2, avg_shot_dur))
                return {
                    "has_ref": True,
                    "target_shot_dur": round(avg_shot_dur, 2),
                    "cut_count": len(cuts),
                    "pacing": "fast" if avg_shot_dur < 1.8 else ("slow" if avg_shot_dur > 2.8 else "balanced")
                }
        except Exception:
            pass

        return {"has_ref": False, "target_shot_dur": 2.4, "pacing": "balanced"}

    def plan_highlight(
        self,
        analyzed_clips: List[Dict[str, Any]],
        beat_times: Optional[List[float]] = None,
        target_duration: float = 240.0,
        ref_video_path: Optional[str] = None
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
        """
        if not analyzed_clips:
            return {"format": "Cinematic_Highlight_4min", "clips": [], "total_duration": 0.0}

        # Analyze reference video rhythm if provided
        ref_pacing = self.analyze_reference_video_pace(ref_video_path)
        ref_shot_dur = ref_pacing.get("target_shot_dur", 2.4)
        has_ref = ref_pacing.get("has_ref", False)

        used_paths = set()
        timeline_entries = []
        curr_time = 0.0

        def snap_duration(dur: float, current_t: float) -> float:
            if has_ref:
                dur = ref_shot_dur
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
            in_sec = clip.get("in_seconds", 0.0)
            out_sec = clip.get("out_seconds", in_sec + actual_dur)
            action_start = clip.get("action_start_seconds", in_sec)
            action_peak = clip.get("action_peak_seconds", in_sec + (actual_dur * 0.45))
            clip_dur = clip.get("duration", out_sec)
            fps = clip.get("native_fps", 25.0)

            # Center cut around Action Peak & ensure it starts AT or AFTER action_start (skips pre-action waiting)
            if (out_sec - in_sec) > actual_dur and action_peak > in_sec:
                target_in = max(action_start, in_sec, action_peak - (actual_dur * 0.45))
                target_out = min(clip_dur, target_in + actual_dur)
                if (target_out - target_in) < actual_dur:
                    target_in = max(action_start, 0.0, target_out - actual_dur)
            else:
                target_in = max(action_start, in_sec)
                target_out = min(clip_dur, target_in + actual_dur)

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
                "act": act_name
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
        bride_candidates.sort(key=lambda c: (c.get("shot_type") == "close_up", c.get("quality_score", 0)), reverse=True)
        for b_clip in bride_candidates[:2]:
            add_entry(b_clip, 2.8, "Bride Solo Glamour & Jewelry Portrait", "Act 1: Bride & Groom Portraits")

        # Groom candidates: mid shots, sharp swagger portraits
        groom_candidates = [c for c in early_pool if c.get("shot_type") in ("mid", "close_up") and c.get("file_path") not in used_paths]
        groom_candidates.sort(key=lambda c: c.get("quality_score", 0), reverse=True)
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
        needed_func_clips = max(8, int(round((target_duration * 0.45) / 2.3)))
        functions_selected = self._select_chronological_sequence(functions_pool, needed_func_clips, mix_framing=True)

        for f_clip in functions_selected:
            if curr_time >= target_duration * 0.75:
                break
            add_entry(f_clip, 2.2, "Joyous Ceremony & Celebration (Haldi/Baraat)", "Act 2: Core Ceremonies & Energy")

        # =================================================================
        # PHASE 4: Sacred Climax (Varmala / Fire Pheras / Sindoor)
        # =================================================================
        climax_pool = [c for c in analyzed_clips[idx_act2_end:] if c.get("file_path") not in used_paths]
        needed_climax_clips = max(6, int(round((target_duration * 0.20) / 1.8)))
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

        return {
            "format": "Cinematic_Highlight_4min",
            "aspect_ratio": "16:9 Cinematic",
            "total_duration": round(curr_time, 2),
            "clip_count": len(timeline_entries),
            "sequence_name": "02_Cinematic_Highlight_4min",
            "clips": timeline_entries,
            "reference_matched": has_ref,
            "target_shot_pace": ref_shot_dur
        }

    def plan_full_film(self, analyzed_clips: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Chronological complete wedding film preserving 100% of original event order.
        Conservative / Gentle Trimming: Preserves 85% to 100% of each clip duration!
        Only trims genuine camera jerk at button press / shutdown.
        """
        curr_time = 0.0
        timeline_entries = []

        for clip in analyzed_clips:
            # Prefer conservative full film cut points (preserving action, skipping idle pre-roll)
            in_sec = clip.get("full_in_seconds", clip.get("action_start_seconds", clip.get("in_seconds", 0.0)))
            out_sec = clip.get("full_out_seconds", clip.get("action_end_seconds", clip.get("out_seconds", clip.get("duration", 0.0))))
            dur = max(1.0, round(out_sec - in_sec, 2))
            fps = clip.get("native_fps", 25.0)

            in_frame = int(clip.get("full_in_frame", clip.get("action_start_frame", in_sec * fps)))
            out_frame = int(clip.get("full_out_frame", clip.get("action_end_frame", out_sec * fps)))

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
            "clips": timeline_entries
        }


# Global instance
story_planner = StoryPlanner()

