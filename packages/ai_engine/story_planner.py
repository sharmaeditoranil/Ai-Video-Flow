"""
AI Video Flow - 3-in-1 Storyboard & Timeline Planner
Constructs edit sequences for:
1. Wedding Teaser (30-60s, fast-paced reel, top 2% shots)
2. Cinematic Highlight (3-5 mins, 3-Act structure, beat-aligned)
3. Full Wedding Film (15-45 mins, chronological ritual preservation)
"""

import math
from typing import List, Dict, Any, Optional


class StoryPlanner:
    """
    Assembles curated, trimmed video clips into cohesive narrative timelines.
    """

    def plan_teaser(self, analyzed_clips: List[Dict[str, Any]], target_duration: float = 60.0, is_vertical: bool = False) -> Dict[str, Any]:
        """
        Creates an ultra-dynamic teaser plan using only top-tier shots.
        Average shot duration: 1.0 - 1.8 seconds.
        """
        # Sort by quality score descending
        sorted_clips = sorted(analyzed_clips, key=lambda c: c.get("quality_score", 0), reverse=True)
        # Select top 15-25 clips
        needed_clips = min(len(sorted_clips), int(math.ceil(target_duration / 1.5)))
        selected = sorted_clips[:needed_clips]

        timeline_entries = []
        curr_time = 0.0

        for idx, clip in enumerate(selected):
            # Clip duration for teaser: fast cut (1.2s - 1.8s)
            clip_cut_dur = 1.4 if idx % 2 == 0 else 1.8
            in_sec = clip.get("in_seconds", 0.0)
            native_fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": clip.get("file_path", "").split("/")[-1],
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + clip_cut_dur, 2),
                "in_frame": int(in_sec * native_fps),
                "out_frame": int((in_sec + clip_cut_dur) * native_fps),
                "duration": clip_cut_dur,
                "role": "Teaser Climax / Hook" if idx < 3 else "High Energy Beat Cut"
            })
            curr_time += clip_cut_dur
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

    def plan_highlight(self, analyzed_clips: List[Dict[str, Any]], beat_times: Optional[List[float]] = None, target_duration: float = 240.0) -> Dict[str, Any]:
        """
        Creates a 3-Act Cinematic Highlight (3 to 5 minutes).
        Act 1: Anticipation & Prep (Slow, 2.5-3.5s cuts)
        Act 2: Core Rituals & Emotions (Mid-tempo, 2.0-3.0s cuts)
        Act 3: Celebration & Finale (Upbeat, 1.2-2.0s cuts)
        """
        sorted_clips = sorted(analyzed_clips, key=lambda c: c.get("quality_score", 0), reverse=True)
        # Allocate into 3 Acts
        total_clips = len(sorted_clips)
        chunk = max(1, total_clips // 3)
        act1_pool = sorted_clips[:chunk]
        act2_pool = sorted_clips[chunk : 2 * chunk]
        act3_pool = sorted_clips[2 * chunk :]

        timeline_entries = []
        curr_time = 0.0

        # Act 1: Prep & Detail Shots (approx 20% of duration)
        act1_limit = target_duration * 0.22
        for clip in act1_pool:
            cut_dur = 3.0
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)
            timeline_entries.append({
                "clip_name": clip.get("file_path", "").split("/")[-1],
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "act": "Act 1: Preparation & Getting Ready"
            })
            curr_time += cut_dur
            if curr_time >= act1_limit:
                break

        # Act 2: Varmala, Pheras & Rituals (approx 50% of duration)
        act2_limit = target_duration * 0.72
        for clip in act2_pool:
            cut_dur = 2.4
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)
            timeline_entries.append({
                "clip_name": clip.get("file_path", "").split("/")[-1],
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "act": "Act 2: Core Ceremonies & Vows"
            })
            curr_time += cut_dur
            if curr_time >= act2_limit:
                break

        # Act 3: Sangeet, Dance & Bidaai (approx 28% of duration)
        for clip in act3_pool:
            cut_dur = 1.6
            in_sec = clip.get("in_seconds", 0.0)
            fps = clip.get("native_fps", 25.0)
            timeline_entries.append({
                "clip_name": clip.get("file_path", "").split("/")[-1],
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cut_dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cut_dur) * fps),
                "duration": cut_dur,
                "act": "Act 3: Celebration & Finale"
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
        Chronological complete wedding film with shaky head and tail trimmed.
        Preserves complete rituals and natural narrative flow.
        """
        # Preserves original chronological sequence (e.g., by filename or capture time)
        curr_time = 0.0
        timeline_entries = []

        for clip in analyzed_clips:
            in_sec = clip.get("in_seconds", 0.0)
            out_sec = clip.get("out_seconds", clip.get("duration", 0.0))
            dur = max(1.0, out_sec - in_sec)
            fps = clip.get("native_fps", 25.0)

            timeline_entries.append({
                "clip_name": clip.get("file_path", "").split("/")[-1],
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + dur, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int(out_sec * fps),
                "duration": round(dur, 2),
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
