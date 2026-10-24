"""
AI Video Flow - B-Roll & Cutaway Placer Engine
Inspired by Ambar AI (Robelob/Ambar-AI-Video-Editor-Plugin-For-Premiere-Pro).
Automatically identifies high-quality B-Roll cutaways from the footage pool
and layers them onto Video Track 2 (V2) without breaking continuous speech/audio on V1/A1.
"""

import os
import math
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.broll_placer")


class BrollPlacer:
    """
    Intelligently identifies and places B-Roll cutaways on Video Track 2 (V2)
    to elevate talking-head, vlog, wedding speech, and podcast timelines.
    """

    def suggest_broll_cutaways(
        self,
        primary_clips: List[Dict[str, Any]],
        all_analyzed_clips: List[Dict[str, Any]],
        target_track: int = 2,
        cutaway_frequency_sec: float = 6.0,
        cutaway_duration_sec: float = 2.4
    ) -> List[Dict[str, Any]]:
        """
        Calculates non-destructive B-roll overlay placements on Video Track 2.
        Excludes clips already placed on V1.
        """
        # 1. Identify paths already used on V1
        v1_paths = {
            os.path.normpath(c.get("file_path", "")).lower()
            for c in primary_clips if c.get("file_path")
        }

        # 2. Candidate pool for B-Roll (unused clips or high-quality detail shots)
        candidates = []
        for c in all_analyzed_clips:
            p = os.path.normpath(c.get("file_path", "")).lower()
            if p not in v1_paths:
                candidates.append(c)

        # If not enough unused clips, use secondary parts of top clips
        if not candidates:
            candidates = sorted(all_analyzed_clips, key=lambda x: x.get("quality_score", 0), reverse=True)

        if not candidates:
            return []

        # Sort candidate pool by quality score
        candidates.sort(key=lambda x: x.get("quality_score", 0), reverse=True)

        # 3. Schedule cutaways over the primary timeline duration
        total_v1_duration = primary_clips[-1].get("timeline_end", 0.0) if primary_clips else 0.0
        if total_v1_duration <= 4.0:
            return []

        placements = []
        curr_time = 4.0  # Start cutaways after introductory 4 seconds of A-roll
        cand_idx = 0

        while curr_time + cutaway_duration_sec < total_v1_duration - 2.0:
            cand = candidates[cand_idx % len(candidates)]
            in_sec = cand.get("in_seconds", 0.0)
            fps = cand.get("native_fps", 25.0)

            placements.append({
                "clip_name": os.path.basename(cand.get("file_path", "")),
                "file_path": cand.get("file_path"),
                "track_index": target_track,
                "timeline_start": round(curr_time, 2),
                "timeline_end": round(curr_time + cutaway_duration_sec, 2),
                "in_frame": int(in_sec * fps),
                "out_frame": int((in_sec + cutaway_duration_sec) * fps),
                "duration": cutaway_duration_sec,
                "role": "B-Roll Cutaway Overlay (V2)"
            })

            cand_idx += 1
            curr_time += cutaway_frequency_sec + cutaway_duration_sec

        logger.info(f"Generated {len(placements)} B-Roll cutaways for Track V{target_track}")
        return placements


# Global singleton instance
broll_placer = BrollPlacer()
