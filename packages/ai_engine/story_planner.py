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
            native_fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * native_fps),
                "out_frame": int((in_sec + cut_dur) * native_fps),
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

    def plan_highlight(
        self,
        analyzed_clips: List[Dict[str, Any]],
        beat_times: Optional[List[float]] = None,
        target_duration: float = 240.0
    ) -> Dict[str, Any]:
        """
        Creates a 3-Act Cinematic Highlight (3 to 5 minutes) strictly following chronological ceremony flow:
        Act 1 (0% - 25% of footage): Anticipation & Opening (Matkor / Preparation / Family Welcoming) -> Pacing: 2.6s - 3.4s
        Act 2 (25% - 70% of footage): Core Ceremonies & Energy (Haldi / Mehndi / Tilak / Baraat) -> Pacing: 2.0s - 2.6s
        Act 3 (70% - 100% of footage): Grand Climax (Varmala / Pheras / Sindoor / Bidaai Finale) -> Pacing: 1.6s - 2.2s

        Wide and Close-Up shots are smoothly alternated within each ritual.
        """
        if not analyzed_clips:
            return {"format": "Cinematic_Highlight_4min", "clips": [], "total_duration": 0.0}

        total_clips = len(analyzed_clips)
        idx_act1_end = max(1, int(total_clips * 0.25))
        idx_act2_end = max(idx_act1_end + 1, int(total_clips * 0.70))

        # Partition clips into strictly chronological ceremony buckets
        act1_clips = analyzed_clips[:idx_act1_end]
        act2_clips = analyzed_clips[idx_act1_end:idx_act2_end]
        act3_clips = analyzed_clips[idx_act2_end:]

        act1_dur_target = target_duration * 0.22
        act2_dur_target = target_duration * 0.50
        act3_dur_target = target_duration * 0.28

        act1_needed = max(3, int(round(act1_dur_target / 3.0)))
        act2_needed = max(6, int(round(act2_dur_target / 2.3)))
        act3_needed = max(5, int(round(act3_dur_target / 1.8)))

        act1_selected = self._select_chronological_sequence(act1_clips, act1_needed, mix_framing=True)
        act2_selected = self._select_chronological_sequence(act2_clips, act2_needed, mix_framing=True)
        act3_selected = self._select_chronological_sequence(act3_clips, act3_needed, mix_framing=True)

        timeline_entries = []
        curr_time = 0.0

        # Helper to snap cut duration to music beat if beats available
        def snap_duration(dur: float, current_t: float) -> float:
            if not beat_times:
                return dur
            target_t = current_t + dur
            # Find closest beat after current_t + 1.2s
            candidates = [b for b in beat_times if b >= current_t + 1.2 and abs(b - target_t) < 0.65]
            if candidates:
                closest_beat = min(candidates, key=lambda b: abs(b - target_t))
                return max(1.2, round(closest_beat - current_t, 2))
            return dur

        # -----------------------------------------------------------------
        # Act 1: Matkor / Getting Ready / Anticipation (Slow, atmospheric cuts)
        # -----------------------------------------------------------------
        act1_time = 0.0
        for clip in act1_selected:
            cut_dur = snap_duration(3.0, curr_time)
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "act": "Act 1: Opening & Preparation (Matkor / Setup)"
            })
            curr_time += cut_dur
            act1_time += cut_dur
            if act1_time >= act1_dur_target:
                break

        # -----------------------------------------------------------------
        # Act 2: Haldi / Mehndi / Tilak / Baraat (Mid-tempo, joyous rituals)
        # -----------------------------------------------------------------
        act2_time = 0.0
        for clip in act2_selected:
            cut_dur = snap_duration(2.3, curr_time)
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "act": "Act 2: Core Ceremonies & Energy (Haldi / Baraat)"
            })
            curr_time += cut_dur
            act2_time += cut_dur
            if act2_time >= act2_dur_target:
                break

        # -----------------------------------------------------------------
        # Act 3: Varmala / Pheras / Bidaai (Celebratory & Emotional Finale)
        # -----------------------------------------------------------------
        for clip in act3_selected:
            cut_dur = snap_duration(1.8, curr_time)
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "shot_type": clip.get("shot_type", "mid"),
                "act": "Act 3: Grand Climax & Finale (Varmala / Pheras / Bidaai)"
            })
            curr_time += cut_dur
            if curr_time >= target_duration:
                break

        return {
            "format": "Cinematic_Highlight_4min",
            "aspect_ratio": "16:9 Cinematic",
            "total_duration": round(curr_time, 2),
            "clip_count": len(timeline_entries),
            "sequence_name": "02_Cinematic_Highlight_4min",
            "clips": timeline_entries
        }

    def plan_full_film(self, analyzed_clips: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Chronological complete wedding film preserving 100% of original event order.
        Shaky start and camera shut-down tails are trimmed cleanly.
        """
        curr_time = 0.0
        timeline_entries = []

        for clip in analyzed_clips:
            in_sec = clip.get("in_seconds", 0.0)
            out_sec = clip.get("out_seconds", clip.get("duration", 0.0))
            dur = max(1.0, out_sec - in_sec)
            fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int(out_sec * fps),
                "duration": round(dur, 2),
                "shot_type": clip.get("shot_type", "mid"),
                "role": "Chronological Film Event"
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
