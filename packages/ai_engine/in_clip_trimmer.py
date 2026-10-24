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
                "trimmed_duration": duration,
                "full_in_frame": 0,
                "full_out_frame": total_frames,
                "full_in_seconds": 0.0,
                "full_out_seconds": duration,
                "full_duration": duration,
                "quality_score": 85.0,
                "shot_type": "mid",
                "reason": "Short clip kept intact"
            }

        # Step rate for sampling
        frame_interval = max(1, int(round(native_fps / self.sample_fps)))
        
        prev_gray = None
        stability_scores = []
        sharpness_scores = []
        sample_frame_indices = []
        luma = []  # brightness of every frame, used to find photographer flashes for multi-camera sync

        curr_frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            luma.append(float(frame[::24, ::24].mean()))

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

        # 1. Full Film Window (Conservative: preserves 85% to 100% of footage)
        full_in_frame, full_out_frame = self._find_full_film_window(
            sample_frame_indices, stability_scores, sharpness_scores,
            native_fps, total_frames, duration
        )

        # 2. Golden Window (Punchy 2.5 - 6s window for Teaser / Fast Highlight)
        in_frame, out_frame, best_score = self._find_golden_window(
            sample_frame_indices, stability_scores, sharpness_scores,
            native_fps, total_frames, duration
        )

        shot_type = "mid"
        try:
            mid_f = (in_frame + out_frame) // 2
            cap.set(cv2.CAP_PROP_POS_FRAMES, mid_f)
            r, mid_img = cap.read()
            if r and mid_img is not None:
                sw = cv2.resize(mid_img, (320, 180))
                ycrcb = cv2.cvtColor(sw, cv2.COLOR_BGR2YCrCb)
                mask = cv2.inRange(ycrcb, np.array([0, 133, 77]), np.array([255, 173, 127]))
                skin_pct = (np.count_nonzero(mask) / mask.size) * 100.0
                if skin_pct > 24.0:
                    shot_type = "close_up"
                elif skin_pct > 7.0:
                    shot_type = "mid"
                else:
                    shot_type = "wide"
        except Exception:
            pass

        cap.release()

        full_dur = round((full_out_frame - full_in_frame) / native_fps, 2)
        trim_dur = round((out_frame - in_frame) / native_fps, 2)

        return {
            "file_path": video_path,
            "duration": round(duration, 2),
            "native_fps": native_fps,
            "total_frames": total_frames,
            # Golden Window (for Teaser & Beat-synced Highlight)
            "in_frame": in_frame,
            "out_frame": out_frame,
            "in_seconds": round(in_frame / native_fps, 2),
            "out_seconds": round(out_frame / native_fps, 2),
            "trimmed_duration": trim_dur,
            "trimmed_head_seconds": round(in_frame / native_fps, 2),
            "trimmed_tail_seconds": round((total_frames - out_frame) / native_fps, 2),
            # Full Film Window (Conservative: keeps 85% - 100% of good footage!)
            "full_in_frame": full_in_frame,
            "full_out_frame": full_out_frame,
            "full_in_seconds": round(full_in_frame / native_fps, 2),
            "full_out_seconds": round(full_out_frame / native_fps, 2),
            "full_duration": full_dur,
            "quality_score": round(best_score, 1),
            "shot_type": shot_type,
            "flash_times": self._flash_times(luma, native_fps),
            "luma_curve": self._luma_curve(luma, native_fps),
        }

    @staticmethod
    def _luma_curve(luma: List[float], fps: float, rate: float = 5.0) -> List[float]:
        """Scene brightness at 5 samples per second, for light-change sync between cameras."""
        step = max(1, int(round(fps / rate)))
        return [round(sum(luma[i:i + step]) / len(luma[i:i + step]), 2) for i in range(0, len(luma), step)]

    @staticmethod
    def _flash_times(luma: List[float], fps: float) -> List[float]:
        """Seconds (from clip start) where a photo flash lit the scene: a 1-3 frame brightness spike."""
        flashes: List[float] = []
        n = len(luma)
        for i in range(6, n - 3):
            base = sorted(luma[i - 6:i])[3]
            if luma[i] - base < 22:
                continue
            if min(luma[i + 1:i + 4]) - base > 12:  # stays bright = lights turned on, not a flash
                continue
            t = i / fps
            if not flashes or t - flashes[-1] > 0.5:
                flashes.append(round(t, 3))
        return flashes

    def _find_full_film_window(
        self,
        sample_indices: List[int],
        stability: List[float],
        sharpness: List[float],
        native_fps: float,
        total_frames: int,
        duration: float
    ) -> Tuple[int, int]:
        """
        Conservative trimmer for Full Wedding Film.
        Preserves 85% to 100% of each clip duration!
        Only trims genuine camera jerk when clicking the REC button at start (max 0.8s)
        or when tilting to floor / stopping at tail (max 0.6s).
        If the camera is already steady, trims 0 seconds.
        """
        if not sample_indices or not stability:
            return 0, total_frames

        n = len(sample_indices)

        # Check head for button-press jerk (first 1.2s max or 10% of clip)
        max_head = min(int(self.sample_fps * 1.2), max(1, int(n * 0.10)))
        start_idx = 0
        for i in range(max_head):
            # Only trim if severe camera jerk / blur right at button press
            if stability[i] < 35.0 or sharpness[i] < 12.0:
                start_idx = i + 1
            else:
                break

        # Check tail for camera shutdown / ground tilt (last 1.0s max or 10% of clip)
        max_tail = min(int(self.sample_fps * 1.0), max(1, int(n * 0.10)))
        end_idx = n - 1
        for i in range(n - 1, max(0, n - 1 - max_tail), -1):
            if stability[i] < 35.0 or sharpness[i] < 12.0:
                end_idx = i - 1
            else:
                break

        if start_idx >= end_idx:
            start_idx = 0
            end_idx = n - 1

        in_f = sample_indices[start_idx]
        out_f = sample_indices[end_idx]

        in_f = max(0, min(in_f, total_frames - 1))
        out_f = max(in_f + int(native_fps * 1.0), min(out_f, total_frames))
        return in_f, out_f

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
        # Trim first 2.5s (camera shake start) and last 1.0s (camera shut down) for teaser/highlight
        head_trim = min(2.5, max(0.0, duration * 0.20))
        tail_trim = min(1.0, max(0.0, duration * 0.10))

        in_sec = head_trim
        out_sec = max(in_sec + 2.0, duration - tail_trim)

        # Full Film Window: Gentle trimming, cuts at most 0.4s start / 0.3s end
        full_head = min(0.4, max(0.0, duration * 0.05)) if duration > 4.0 else 0.0
        full_tail = min(0.3, max(0.0, duration * 0.04)) if duration > 4.0 else 0.0
        full_in_sec = full_head
        full_out_sec = max(full_in_sec + 1.5, duration - full_tail)

        return {
            "file_path": video_path,
            "duration": round(duration, 2),
            "native_fps": round(fps, 2),
            "total_frames": total_frames,
            # Golden Window
            "in_frame": int(in_sec * fps),
            "out_frame": int(out_sec * fps),
            "in_seconds": round(in_sec, 2),
            "out_seconds": round(out_sec, 2),
            "trimmed_duration": round(out_sec - in_sec, 2),
            "trimmed_head_seconds": round(in_sec, 2),
            "trimmed_tail_seconds": round(duration - out_sec, 2),
            # Full Film Window (Conservative: preserves 90-100% of footage)
            "full_in_frame": int(full_in_sec * fps),
            "full_out_frame": int(full_out_sec * fps),
            "full_in_seconds": round(full_in_sec, 2),
            "full_out_seconds": round(full_out_sec, 2),
            "full_duration": round(full_out_sec - full_in_sec, 2),
            "quality_score": 82.5,
            "shot_type": "mid",
            "engine": "Smart Trimmer Fallback"
        }


# Global instance
in_clip_trimmer = InClipTrimmer()
