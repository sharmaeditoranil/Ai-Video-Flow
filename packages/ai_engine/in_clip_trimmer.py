"""
AI Video Flow - In-Clip Trimmer & Quality Analyzer
Analyzes raw wedding footage, detects camera shake (Optical Flow),
lens focus hunting/blur (Laplacian), and extracts the optimal steady
'Golden Window' (In-Point & Out-Point) discarding shaky head/tail.
"""

import os
import cv2
import numpy as np
import logging
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger("ai_video_flow.in_clip_trimmer")


class InClipTrimmer:
    """
    Evaluates individual video clips to find the highest-quality,
    shake-free, in-focus sub-segment.
    """

    def __init__(self, sample_fps: int = 10, min_subclip_duration: float = 2.5, max_subclip_duration: float = 6.0):
        self.sample_fps = sample_fps
        self.min_subclip_duration = min_subclip_duration
        self.max_subclip_duration = max_subclip_duration

    def analyze_clip(self, video_path: str) -> Dict[str, Any]:
        """
        Processes video frames to calculate stability and sharpness curves.
        Returns optimal in/out points and quality metadata.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"OpenCV could not open {video_path}. Using smart metadata trimmer fallback.")
            return self._fallback_smart_trim(video_path)

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / native_fps if native_fps > 0 else 0

        # If clip is very short (< 3s), use it whole
        if duration <= self.min_subclip_duration:
            cap.release()
            return {
                "file_path": video_path,
                "duration": duration,
                "total_frames": total_frames,
                "in_frame": 0,
                "out_frame": total_frames,
                "in_seconds": 0.0,
                "out_seconds": duration,
                "quality_score": 75.0,
                "reason": "Short clip kept intact"
            }

        # Step rate for sampling
        frame_interval = max(1, int(round(native_fps / self.sample_fps)))
        
        prev_gray = None
        stability_scores = []
        sharpness_scores = []
        sample_frame_indices = []

        curr_frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if curr_frame_idx % frame_interval == 0:
                # Resize frame for ultra-fast processing
                h, w = frame.shape[:2]
                target_w = 480
                target_h = int(h * (target_w / w))
                small = cv2.resize(frame, (target_w, target_h))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

                # 1. Sharpness / Focus score (Laplacian Variance)
                lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
                # Normalized sharpness (typical values 50 - 500+)
                sharpness_norm = min(100.0, max(0.0, (lap_var / 300.0) * 100.0))
                sharpness_scores.append(sharpness_norm)

                # 2. Stability / Camera Shake Score (Optical Flow)
                if prev_gray is not None:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray, gray, None, 
                        pyr_scale=0.5, levels=2, winsize=15, 
                        iterations=2, poly_n=5, poly_sigma=1.1, flags=0
                    )
                    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    mean_mag = np.mean(mag)
                    std_mag = np.std(mag)
                    # Sudden erratic jerk has high std/mean
                    jerk_factor = mean_mag + 1.5 * std_mag
                    # Inverted: lower jerk = higher stability
                    stability_norm = max(0.0, min(100.0, 100.0 - (jerk_factor * 8.0)))
                    stability_scores.append(stability_norm)
                else:
                    stability_scores.append(85.0)

                prev_gray = gray
                sample_frame_indices.append(curr_frame_idx)

            curr_frame_idx += 1

        cap.release()

        # Find the optimal Golden Window
        in_frame, out_frame, best_score = self._find_golden_window(
            sample_frame_indices, stability_scores, sharpness_scores,
            native_fps, total_frames, duration
        )

        return {
            "file_path": video_path,
            "duration": round(duration, 2),
            "native_fps": native_fps,
            "total_frames": total_frames,
            "in_frame": in_frame,
            "out_frame": out_frame,
            "in_seconds": round(in_frame / native_fps, 2),
            "out_seconds": round(out_frame / native_fps, 2),
            "trimmed_duration": round((out_frame - in_frame) / native_fps, 2),
            "trimmed_head_seconds": round(in_frame / native_fps, 2),
            "trimmed_tail_seconds": round((total_frames - out_frame) / native_fps, 2),
            "quality_score": round(best_score, 1)
        }

    def _find_golden_window(
        self,
        sample_indices: List[int],
        stability: List[float],
        sharpness: List[float],
        native_fps: float,
        total_frames: int,
        duration: float
    ) -> Tuple[int, int, float]:
        """
        Uses sliding window over the combined quality curve to find
        the continuous section with maximum stability & clarity.
        """
        if not stability or not sharpness:
            # Fallback: trim default 15% from head and 10% from tail
            in_f = int(total_frames * 0.15)
            out_f = int(total_frames * 0.90)
            return in_f, out_f, 70.0

        n = len(stability)
        combined = [0.6 * s + 0.4 * f for s, f in zip(stability, sharpness)]

        window_samples = max(2, int(round(self.min_subclip_duration * self.sample_fps)))
        max_samples = int(round(self.max_subclip_duration * self.sample_fps))

        best_score = -1.0
        best_start_idx = 0
        best_end_idx = min(n - 1, window_samples)

        # Slide window of variable size between min and max duration
        for w_size in range(window_samples, min(n, max_samples) + 1):
            for i in range(n - w_size + 1):
                window_scores = combined[i : i + w_size]
                avg_score = float(np.mean(window_scores))
                min_score = float(np.min(window_scores))

                # Penalty for start of clip (cameraman hand start jerk)
                start_penalty = 10.0 if i < int(self.sample_fps * 1.5) else 0.0
                # Penalty for end of clip (cameraman looking down before stop)
                end_penalty = 10.0 if (i + w_size) > (n - int(self.sample_fps * 1.0)) else 0.0

                composite = (avg_score * 0.7 + min_score * 0.3) - start_penalty - end_penalty

                if composite > best_score:
                    best_score = composite
                    best_start_idx = i
                    best_end_idx = i + w_size - 1

        in_frame = sample_indices[best_start_idx]
        out_frame = sample_indices[min(len(sample_indices) - 1, best_end_idx)]

        # Ensure bounds
        in_frame = max(0, min(in_frame, total_frames - 1))
        out_frame = max(in_frame + int(native_fps * 1.5), min(out_frame, total_frames))

        return in_frame, out_frame, max(0.0, min(100.0, best_score))


    def _fallback_smart_trim(self, video_path: str) -> Dict[str, Any]:
        """
        Resilient heuristic fallback using FFprobe / file stat
        when OpenCV is sandboxed from directly reading the path.
        """
        import subprocess
        fps = 25.0
        duration = 10.0

        try:
            # Try ffprobe
            cmd = [
                "/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=r_frame_rate,nb_frames", "-of", "json", video_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                duration = float(data.get("format", {}).get("duration", 10.0))
                streams = data.get("streams", [])
                if streams:
                    r_fps = streams[0].get("r_frame_rate", "25/1")
                    if "/" in r_fps:
                        num, den = r_fps.split("/")
                        fps = float(num) / float(den) if float(den) > 0 else 25.0
        except Exception:
            pass

        total_frames = int(duration * fps)
        # Trim first 3.5s (camera shake start) and last 1.2s (camera shut down)
        head_trim = min(3.5, max(0.0, duration * 0.25))
        tail_trim = min(1.2, max(0.0, duration * 0.10))

        in_sec = head_trim
        out_sec = max(in_sec + 2.0, duration - tail_trim)

        return {
            "file_path": video_path,
            "duration": round(duration, 2),
            "native_fps": round(fps, 2),
            "total_frames": total_frames,
            "in_frame": int(in_sec * fps),
            "out_frame": int(out_sec * fps),
            "in_seconds": round(in_sec, 2),
            "out_seconds": round(out_sec, 2),
            "trimmed_duration": round(out_sec - in_sec, 2),
            "trimmed_head_seconds": round(in_sec, 2),
            "trimmed_tail_seconds": round(duration - out_sec, 2),
            "quality_score": 82.5,
            "engine": "Smart Trimmer Fallback"
        }


# Global instance
in_clip_trimmer = InClipTrimmer()
