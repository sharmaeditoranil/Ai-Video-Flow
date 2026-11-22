"""
AI Video Flow - In-Clip Trimmer & Quality Analyzer
Analyzes raw wedding footage, detects camera shake (Optical Flow),
lens focus hunting/blur (Laplacian), and extracts the optimal steady
'Golden Window' (In-Point & Out-Point) discarding shaky head/tail.
"""

import os
import re
import json
import math
import hashlib
import threading
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

    def __init__(self, sample_fps: int = 10, min_subclip_duration: float = 2.5, max_subclip_duration: float = 6.0,
                 cache_dir: Optional[str] = None):
        self.sample_fps = sample_fps
        self.min_subclip_duration = min_subclip_duration
        self.max_subclip_duration = max_subclip_duration
        self.cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output", ".analysis_cache")

    def analyze_clip(self, video_path: str, use_cache: bool = True, wedding: bool = True) -> Dict[str, Any]:
        """
        Processes video frames to calculate stability and sharpness curves.
        Returns optimal in/out points and quality metadata, plus the wedding fields (faces, shot_type
        from face size, camera_move, audio_type, moment, wedding_score) when `wedding` is on.
        Results are cached per file content, so a second run on the same footage is instant.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        key = _content_key(video_path) if use_cache else None
        if key and self.sample_fps != 10:
            key = f"{key}_s{self.sample_fps}"  # light / custom sampling never mixes with the full analysis
        if key:
            cached = _cache_read(self.cache_dir, key)
            if cached is not None and (cached.get("wedding_version") or not wedding):
                cached["file_path"] = video_path
                cached["from_cache"] = True
                return cached

        ext = os.path.splitext(video_path)[1].lower()
        if ext in STILL_EXTS:
            result = self._analyze_still_clip(video_path)
        else:
            result = self._analyze_video(video_path)
        motion = result.pop("_motion", None)
        if wedding:
            try:
                enrich_wedding_fields(result, motion)
            except Exception as e:  # never lose the base analysis because of the extra fields
                logger.warning(f"Wedding analysis skipped for {video_path}: {e}")
        if key:
            _cache_write(self.cache_dir, key, result)
        return result

    def _analyze_video(self, video_path: str) -> Dict[str, Any]:

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
                "native_fps": native_fps,
                "total_frames": total_frames,
                "in_frame": 0,
                "out_frame": total_frames,
                "in_seconds": 0.0,
                "out_seconds": duration,
                "action_start_seconds": 0.0,
                "action_start_frame": 0,
                "action_end_seconds": duration,
                "action_end_frame": total_frames,
                "action_peak_seconds": round(duration * 0.5, 2),
                "action_peak_frame": total_frames // 2,
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
        prev_mag = None
        stability_scores = []
        sharpness_scores = []
        action_scores = []
        sample_frame_indices = []
        luma = []  # brightness of every frame, used to find photographer flashes for multi-camera sync
        motion_samples: List[Tuple[int, float, float, float, float, float, float]] = []

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
                sharpness_norm = min(100.0, max(0.0, (lap_var / 300.0) * 100.0))
                sharpness_scores.append(sharpness_norm)

                # 2. Stability & Purposeful Action Detection (Optical Flow)
                if prev_gray is not None:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray, gray, None, 
                        pyr_scale=0.5, levels=2, winsize=15, 
                        iterations=2, poly_n=5, poly_sigma=1.1, flags=0
                    )
                    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    mean_mag = float(np.mean(mag))
                    std_mag = float(np.std(mag))

                    # Camera Jerk vs Smooth Cinematic Motion (acceleration + variance)
                    accel = abs(mean_mag - prev_mag) if prev_mag is not None else 0.0
                    jerk = (accel * 3.5) + (std_mag * 1.5)
                    stability_norm = max(0.0, min(100.0, 100.0 - (jerk * 6.0)))
                    stability_scores.append(stability_norm)
                    motion_samples.append(_flow_sample(flow, curr_frame_idx, stability_norm))

                    # 3. Purposeful Action / Movement Recognition
                    # Wedding actions (subject moving, smiling, turning, garland, dancing, eye contact):
                    # - If mean_mag < 0.40: Dead static / actor just standing waiting for cue (low score)
                    # - If 0.60 <= mean_mag <= 5.5: Golden Action Zone (actor actively performing action!)
                    # - If mean_mag > 12.0: Erratic camera jerk / fast ground pan (penalized)
                    if mean_mag < 0.40:
                        action_norm = 15.0 + (mean_mag / 0.40) * 20.0
                    elif mean_mag <= 5.0:
                        action_norm = 80.0 + min(20.0, (mean_mag - 0.40) * 5.0)
                    else:
                        action_norm = max(10.0, 100.0 - (mean_mag - 5.0) * 8.0)

                    action_scores.append(action_norm)
                    prev_mag = mean_mag
                else:
                    stability_scores.append(85.0)
                    action_scores.append(50.0)
                    prev_mag = 0.0

                prev_gray = gray
                sample_frame_indices.append(curr_frame_idx)

            curr_frame_idx += 1

        # 1. Discover Action Boundaries (action start onset, action end finish, action peak)
        action_start_frame, action_end_frame, action_peak_frame = self._find_action_boundaries(
            sample_frame_indices, action_scores, stability_scores, sharpness_scores,
            native_fps, total_frames, duration
        )

        # 2. Full Film Window: trims pre-action waiting prelude, keeps 100% of real action
        full_in_frame, full_out_frame = self._find_full_film_window(
            sample_frame_indices, stability_scores, sharpness_scores,
            native_fps, total_frames, duration,
            action_start_frame=action_start_frame, action_end_frame=action_end_frame
        )

        # 3. Golden Window: selects the peak active action segment (starts AT or AFTER action start)
        in_frame, out_frame, best_score, action_peak_frame = self._find_golden_window(
            sample_frame_indices, stability_scores, sharpness_scores, action_scores,
            native_fps, total_frames, duration,
            action_start_frame=action_start_frame, action_end_frame=action_end_frame,
            action_peak_frame=action_peak_frame
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
            "action_start_seconds": round(action_start_frame / native_fps, 2),
            "action_start_frame": action_start_frame,
            "action_end_seconds": round(action_end_frame / native_fps, 2),
            "action_end_frame": action_end_frame,
            "action_peak_seconds": round(action_peak_frame / native_fps, 2),
            "action_peak_frame": action_peak_frame,
            "trimmed_duration": trim_dur,
            "trimmed_head_seconds": round(in_frame / native_fps, 2),
            "trimmed_tail_seconds": round((total_frames - out_frame) / native_fps, 2),
            # Full Film Window (Action preserved: trims pre-action waiting prelude!)
            "full_in_frame": full_in_frame,
            "full_out_frame": full_out_frame,
            "full_in_seconds": round(full_in_frame / native_fps, 2),
            "full_out_seconds": round(full_out_frame / native_fps, 2),
            "full_duration": full_dur,
            "quality_score": round(best_score, 1),
            "shot_type": shot_type,
            "flash_times": self._flash_times(luma, native_fps),
            "luma_curve": self._luma_curve(luma, native_fps),
            "_motion": motion_samples,
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

    def _find_action_boundaries(
        self,
        sample_indices: List[int],
        action: List[float],
        stability: List[float],
        sharpness: List[float],
        native_fps: float,
        total_frames: int,
        duration: float
    ) -> Tuple[int, int, int]:
        """
        Detects purposeful action boundary within the footage:
        - action_start_frame: where subject actually begins motion (removes pre-action idle waiting).
        - action_end_frame: where action concludes (removes dead tail / camera floor drop).
        - action_peak_frame: maximum purposeful action frame.
        """
        if not sample_indices or not action:
            return 0, total_frames, total_frames // 2

        n = len(sample_indices)
        if n < 4:
            return 0, total_frames, total_frames // 2

        peak_idx = int(np.argmax(action))
        peak_score = float(action[peak_idx])

        # Clip has purposeful action if peak energy is active
        has_distinct_action = (peak_score >= 52.0)
        head_min_idx = min(int(round(self.sample_fps * 0.8)), n // 4)

        if has_distinct_action:
            # Action onset: scan forward from start to peak_idx
            # Find the first index where movement begins and is sustained
            onset_idx = head_min_idx
            for i in range(head_min_idx, peak_idx + 1):
                next_score = action[min(n - 1, i + 1)]
                if action[i] >= 45.0 or (action[i] >= 38.0 and next_score >= 45.0):
                    # Natural 0.3s breath cushion so movement begins cleanly
                    cushion = int(round(self.sample_fps * 0.3))
                    onset_idx = max(head_min_idx, i - cushion)
                    break

            # Action conclusion: scan backward from tail towards peak_idx
            tail_max_idx = max(peak_idx, n - 1 - min(int(round(self.sample_fps * 0.8)), n // 4))
            finish_idx = tail_max_idx
            for j in range(tail_max_idx, peak_idx - 1, -1):
                prev_score = action[max(0, j - 1)]
                if action[j] >= 45.0 or (action[j] >= 38.0 and prev_score >= 45.0):
                    cushion = int(round(self.sample_fps * 0.3))
                    finish_idx = min(n - 1, j + cushion)
                    break

            start_f = sample_indices[onset_idx]
            end_f = sample_indices[finish_idx]
        else:
            # Static / scenery shot: trim initial REC jerk and shutdown jerk only
            start_idx = 0
            for i in range(min(int(self.sample_fps * 1.0), n // 4)):
                if stability[i] < 35.0 or sharpness[i] < 12.0:
                    start_idx = i + 1
                else:
                    break
            end_idx = n - 1
            for j in range(n - 1, max(0, n - 1 - min(int(self.sample_fps * 1.0), n // 4)), -1):
                if stability[j] < 35.0 or sharpness[j] < 12.0:
                    end_idx = j - 1
                else:
                    break
            start_f = sample_indices[start_idx]
            end_f = sample_indices[end_idx]

        peak_f = sample_indices[peak_idx]
        start_f = max(0, min(start_f, total_frames - 1))
        end_f = max(start_f + int(native_fps * 1.2), min(end_f, total_frames))
        peak_f = max(start_f, min(peak_f, end_f))
        return start_f, end_f, peak_f

    def _find_full_film_window(
        self,
        sample_indices: List[int],
        stability: List[float],
        sharpness: List[float],
        native_fps: float,
        total_frames: int,
        duration: float,
        action_start_frame: Optional[int] = None,
        action_end_frame: Optional[int] = None
    ) -> Tuple[int, int]:
        """
        Trimmer for Full Wedding Film.
        Starts cleanly where action begins, discarding pre-action waiting prelude!
        """
        if action_start_frame is not None and action_end_frame is not None:
            return action_start_frame, action_end_frame

        if not sample_indices or not stability:
            return 0, total_frames

        n = len(sample_indices)
        max_head = min(int(self.sample_fps * 1.2), max(1, int(n * 0.10)))
        start_idx = 0
        for i in range(max_head):
            if stability[i] < 35.0 or sharpness[i] < 12.0:
                start_idx = i + 1
            else:
                break

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
        action: List[float],
        native_fps: float,
        total_frames: int,
        duration: float,
        action_start_frame: Optional[int] = None,
        action_end_frame: Optional[int] = None,
        action_peak_frame: Optional[int] = None
    ) -> Tuple[int, int, float, int]:
        """
        Finds the continuous section with optimal ACTIVE ACTION, focus sharpness & smooth motion.
        Constrained to start AT or AFTER action start, ensuring no idle waiting prelude enters.
        """
        if not stability or not sharpness or not action:
            in_f = int(total_frames * 0.20)
            out_f = int(total_frames * 0.85)
            peak_f = int((in_f + out_f) / 2)
            return in_f, out_f, 70.0, peak_f

        n = len(stability)
        combined = [
            (0.40 * act) + (0.35 * f) + (0.25 * s)
            for act, f, s in zip(action, sharpness, stability)
        ]

        window_samples = max(2, int(round(self.min_subclip_duration * self.sample_fps)))
        max_samples = int(round(self.max_subclip_duration * self.sample_fps))

        # Find sample index corresponding to action_start_frame
        min_start_idx = 0
        if action_start_frame is not None:
            for idx, f in enumerate(sample_indices):
                if f >= action_start_frame:
                    min_start_idx = idx
                    break

        best_score = -1.0
        best_start_idx = min_start_idx
        best_end_idx = min(n - 1, min_start_idx + window_samples)

        # Slide window of variable size between min and max duration
        for w_size in range(window_samples, min(n, max_samples) + 1):
            # Enforce that search starts at or after action start
            search_start = min_start_idx if min_start_idx + w_size <= n else max(0, n - w_size)
            for i in range(search_start, n - w_size + 1):
                window_scores = combined[i : i + w_size]
                avg_score = float(np.mean(window_scores))
                min_score = float(np.min(window_scores))
                avg_action = float(np.mean(action[i : i + w_size]))

                # Penalty if window is idle / waiting (actor standing still before action starts)
                idle_penalty = 35.0 if avg_action < 42.0 else 0.0
                # Penalty for start of clip (cameraman hand start jerk)
                start_penalty = 12.0 if i < int(self.sample_fps * 1.5) else 0.0
                # Penalty for end of clip (cameraman looking down before stop)
                end_penalty = 12.0 if (i + w_size) > (n - int(self.sample_fps * 1.0)) else 0.0

                composite = (avg_score * 0.75 + min_score * 0.25) - idle_penalty - start_penalty - end_penalty

                if composite > best_score:
                    best_score = composite
                    best_start_idx = i
                    best_end_idx = i + w_size - 1

        # Identify action peak frame within the chosen window
        rel_peak = int(np.argmax(action[best_start_idx : best_end_idx + 1]))
        peak_idx = best_start_idx + rel_peak
        chosen_peak_frame = sample_indices[peak_idx]

        in_frame = sample_indices[best_start_idx]
        out_frame = sample_indices[min(len(sample_indices) - 1, best_end_idx)]

        # Ensure bounds
        in_frame = max(0, min(in_frame, total_frames - 1))
        out_frame = max(in_frame + int(native_fps * 1.5), min(out_frame, total_frames))

        return in_frame, out_frame, max(0.0, min(100.0, best_score)), chosen_peak_frame


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
        # Skip initial 2.2s (actor prep / camera settling) and last 1.0s (stop)
        head_trim = min(2.5, max(1.0, duration * 0.22)) if duration > 4.5 else 0.0
        tail_trim = min(1.0, max(0.0, duration * 0.10))

        in_sec = head_trim
        out_sec = max(in_sec + 2.0, duration - tail_trim)
        action_peak_sec = round(in_sec + (out_sec - in_sec) * 0.45, 2)
        action_peak_frame = int(action_peak_sec * fps)

        # Full Film Window: Gentle trimming, cuts at most 0.4s start / 0.3s end
        full_head = min(0.4, max(0.0, duration * 0.05)) if duration > 4.0 else 0.0
        full_tail = min(0.3, max(0.0, duration * 0.04)) if duration > 4.0 else 0.0

        full_in_sec = full_head
        full_out_sec = max(full_in_sec + 1.0, duration - full_tail)

        return {
            "file_path": video_path,
            "duration": round(duration, 2),
            "native_fps": fps,
            "total_frames": total_frames,
            "in_frame": int(in_sec * fps),
            "out_frame": int(out_sec * fps),
            "in_seconds": round(in_sec, 2),
            "out_seconds": round(out_sec, 2),
            "action_start_seconds": round(in_sec, 2),
            "action_start_frame": int(in_sec * fps),
            "action_end_seconds": round(out_sec, 2),
            "action_end_frame": int(out_sec * fps),
            "action_peak_seconds": action_peak_sec,
            "action_peak_frame": action_peak_frame,
            "trimmed_duration": round(out_sec - in_sec, 2),
            "full_in_frame": int(full_in_sec * fps),
            "full_out_frame": int(full_out_sec * fps),
            "full_in_seconds": round(full_in_sec, 2),
            "full_out_seconds": round(full_out_sec, 2),
            "full_duration": round(full_out_sec - full_in_sec, 2),
            "quality_score": 75.0,
            "shot_type": "mid"
        }

    def _analyze_still_clip(self, file_path: str) -> Dict[str, Any]:
        """
        Processes still images and PSD files for motion graphics and timeline animation.
        Assigns standard 5.0s still frame duration, extracts dimensions, and sets high quality score.
        """
        ext = os.path.splitext(file_path)[1].lower()
        is_psd = ext in {".psd", ".psb"}
        width, height = 1920, 1080
        fps = 25.0
        duration = 5.0
        total_frames = int(duration * fps)

        try:
            from PIL import Image
            with Image.open(file_path) as img:
                width, height = img.size
        except Exception:
            pass

        return {
            "file_path": file_path,
            "duration": duration,
            "native_fps": fps,
            "total_frames": total_frames,
            "width": width,
            "height": height,
            "is_still": True,
            "is_psd": is_psd,
            "in_frame": 0,
            "out_frame": total_frames,
            "in_seconds": 0.0,
            "out_seconds": duration,
            "action_start_seconds": 0.0,
            "action_start_frame": 0,
            "action_end_seconds": duration,
            "action_end_frame": total_frames,
            "action_peak_seconds": round(duration * 0.5, 2),
            "action_peak_frame": total_frames // 2,
            "trimmed_duration": duration,
            "trimmed_head_seconds": 0.0,
            "trimmed_tail_seconds": 0.0,
            "full_in_frame": 0,
            "full_out_frame": total_frames,
            "full_in_seconds": 0.0,
            "full_out_seconds": duration,
            "full_duration": duration,
            "quality_score": 95.0,
            "shot_type": "graphic" if (is_psd or ext == ".png") else "still",
            "reason": "PSD layer asset for motion graphics" if is_psd else "Still image asset",
            "flash_times": [],
            "luma_curve": [50.0] * int(duration * 5),
        }


# ======================================================================================================
# Wedding shot understanding (STEP 1): faces, shot size, camera move, sound, ritual moment, wedding score.
# Everything runs offline on the CPU. Heuristic parts are marked as such and are meant to be replaced by
# better on-device models later without changing the field names.
# ======================================================================================================

STILL_EXTS = {".psd", ".psb", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".svg"}
WEDDING_VERSION = 1          # bump when the wedding fields change, so old cache entries are refreshed
CACHE_VERSION = 1            # bump when the base analysis changes
_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
YUNET_MODEL = os.path.join(_MODEL_DIR, "face_detection_yunet_2023mar.onnx")


# ------------------------------------------------------------------ cache (per file content)

def _content_key(path: str) -> Optional[str]:
    """Hash of size + first and last MB: survives renames / moves, changes when the file changes."""
    try:
        size = os.path.getsize(path)
        h = hashlib.sha1(f"v{CACHE_VERSION}|{size}".encode())
        with open(path, "rb") as f:
            h.update(f.read(1 << 20))
            if size > (2 << 20):
                f.seek(-(1 << 20), os.SEEK_END)
                h.update(f.read(1 << 20))
        return h.hexdigest()
    except OSError:
        return None


def _cache_read(cache_dir: str, key: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(cache_dir, key + ".json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("wedding_version") not in (None, WEDDING_VERSION):
            return None
        return data
    except (OSError, ValueError):
        return None


def _cache_write(cache_dir: str, key: str, result: Dict[str, Any]) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = os.path.join(cache_dir, key + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, default=float)
        os.replace(tmp, os.path.join(cache_dir, key + ".json"))
    except OSError as e:
        logger.debug(f"analysis cache not written: {e}")


# ------------------------------------------------------------------ camera move (from optical flow)

def _flow_sample(flow: np.ndarray, frame_idx: int, stability: float) -> Tuple[int, float, float, float, float, float, float]:
    """(frame, mean dx, mean dy, divergence, centre dx, edge dx, stability) of one optical-flow field."""
    h, w = flow.shape[:2]
    fx, fy = flow[..., 0], flow[..., 1]
    ys, xs = np.mgrid[0:h:4, 0:w:4]
    xn = (xs - w / 2.0) / (w / 2.0)
    yn = (ys - h / 2.0) / (h / 2.0)
    div = float(np.mean(fx[::4, ::4] * xn + fy[::4, ::4] * yn))
    third = max(1, w // 3)
    centre = float(np.mean(fx[:, third:2 * third]))
    edges = float((np.mean(fx[:, :third]) + np.mean(fx[:, 2 * third:])) / 2.0)
    return (int(frame_idx), float(np.mean(fx)), float(np.mean(fy)), div, centre, edges, float(stability))


DRONE_HINT = re.compile(r"(^|[^a-z])(dji|drone|mavic|aerial|avata|fpv)([^a-z]|$)")


def classify_camera_move(samples: Optional[List[Any]], path: str = "", start_frame: int = 0,
                         end_frame: Optional[int] = None) -> str:
    """static / pan / tilt / push_in / pull_out / orbit / drone / shaky for the chosen part of a clip.
    Rules on the optical flow (pixels per sample at 480 px width). Drone comes from the file / folder name."""
    name = path.lower().replace("\\", "/")
    rows = [r for r in (samples or []) if r[0] >= start_frame and (end_frame is None or r[0] <= end_frame)] or list(samples or [])
    if len(rows) < 2:
        return "drone" if DRONE_HINT.search(name) else "static"
    a = np.asarray([r[1:] for r in rows], dtype=float)
    fx, fy, div, cfx, efx, stab = (a[:, i] for i in range(6))
    if np.median(stab) < 50:
        return "shaky"
    if DRONE_HINT.search(name):
        return "drone"
    mfx, mfy, mdiv = float(np.mean(fx)), float(np.mean(fy)), float(np.mean(div))
    mag = math.hypot(mfx, mfy)
    mc, me = float(np.mean(cfx)), float(np.mean(efx))
    if mc * me < 0 and min(abs(mc), abs(me)) > 0.3:
        # subject and background move opposite ways: parallax around the subject. Checked before
        # "static" because the two halves cancel out in the overall average.
        return "orbit"
    if mag < 0.25 and abs(mdiv) < 0.12:
        return "static"
    if abs(mdiv) >= 0.12 and abs(mdiv) > 0.5 * mag:
        return "push_in" if mdiv > 0 else "pull_out"
    return "pan" if abs(mfx) >= abs(mfy) else "tilt"


# ------------------------------------------------------------------ faces (YuNet, on-device)

_DETECTORS: Dict[Tuple[int, int, int], Any] = {}
_DET_LOCK = threading.Lock()


def _detector(w: int, h: int):
    if not os.path.isfile(YUNET_MODEL) or not hasattr(cv2, "FaceDetectorYN"):
        return None
    key = (threading.get_ident(), w, h)
    with _DET_LOCK:
        det = _DETECTORS.get(key)
        if det is None:
            det = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (w, h), 0.6, 0.3, 50)
            _DETECTORS[key] = det
    return det


def _clamp01(v: float) -> float:
    return float(max(0.0, min(1.0, v)))


def detect_faces_raw(frame_bgr: np.ndarray, max_width: int = 640) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """(the frame scaled to at most max_width, raw YuNet rows for that scaled frame or None)."""
    h0, w0 = frame_bgr.shape[:2]
    scale = min(1.0, max_width / float(w0))
    img = cv2.resize(frame_bgr, (int(w0 * scale), int(h0 * scale))) if scale < 1.0 else frame_bgr
    h, w = img.shape[:2]
    det = _detector(w, h)
    if det is None:
        return img, None
    _, raw = det.detect(img)
    return img, raw


def detect_faces(frame_bgr: np.ndarray, max_width: int = 640) -> List[Dict[str, Any]]:
    """Faces in one frame, biggest first: box (fractions of the frame), confidence, sharpness 0-100,
    eyes_open 0-1 and smile 0-1. Eyes / smile are estimates from the 5 YuNet landmarks."""
    img, raw = detect_faces_raw(frame_bgr, max_width)
    if raw is None:
        return []
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = []
    for r in raw:
        x, y, fw, fh = (float(v) for v in r[:4])
        if fw < w * 0.015 or fh < 6:
            continue
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(w, int(x + fw)), min(h, int(y + fh))
        crop = gray[y0:y1, x0:x1]
        if crop.size < 36:
            continue
        crop_n = cv2.resize(crop, (96, max(8, int(96 * crop.shape[0] / max(1, crop.shape[1])))))
        sharp = _clamp01(cv2.Laplacian(crop_n, cv2.CV_64F).var() / 250.0) * 100.0
        re_, le_, rm, lm = (r[4], r[5]), (r[6], r[7]), (r[10], r[11]), (r[12], r[13])
        eye_d = math.hypot(le_[0] - re_[0], le_[1] - re_[1]) or 1.0
        face_std = float(np.std(crop)) + 1.0
        eye_scores = []
        for ex, ey in (re_, le_):   # an open eye has a dark iris against the white: high local contrast
            half = max(2, int(eye_d * 0.22))
            patch = gray[max(0, int(ey) - half):int(ey) + half, max(0, int(ex) - half):int(ex) + half]
            if patch.size:
                eye_scores.append(float(np.std(patch)) / face_std)
        eyes_open = _clamp01((np.mean(eye_scores) - 0.35) / 0.4) if eye_scores else 0.5
        mouth_ratio = math.hypot(lm[0] - rm[0], lm[1] - rm[1]) / eye_d
        smile = _clamp01((mouth_ratio - 0.85) / 0.35)  # mouth widens when smiling
        faces.append({"box": [round(x / w, 4), round(y / h, 4), round(fw / w, 4), round(fh / h, 4)],
                      "confidence": round(float(r[14]), 3), "sharpness": round(sharp, 1),
                      "eyes_open": round(eyes_open, 2), "smile": round(smile, 2)})
    faces.sort(key=lambda f: f["box"][3], reverse=True)
    return faces


def shot_type_from_faces(biggest_face_h: float, face_count: int, bokeh: float = 0.0) -> str:
    """Shot size from the biggest face height (fraction of frame height). Without faces: detail when the
    centre is sharp and the edges soft (rings, mehndi hands, decor details), otherwise wide."""
    if face_count == 0:
        return "detail" if bokeh >= 2.5 else "wide"
    if biggest_face_h >= 0.30:
        return "close_up"
    if biggest_face_h >= 0.12:
        return "mid"
    if biggest_face_h >= 0.05:
        return "wide"
    return "extreme_wide"


def _bokeh_ratio(gray: np.ndarray) -> float:
    h, w = gray.shape[:2]
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_64F))
    centre = lap[h // 4:3 * h // 4, w // 4:3 * w // 4].mean()
    edge = (lap[:h // 6].mean() + lap[-h // 6:].mean() + lap[:, :w // 6].mean() + lap[:, -w // 6:].mean()) / 4.0
    return float(centre / (edge + 1e-3))


# ------------------------------------------------------------------ colour / light cues and ritual moment

def visual_cues(frame_bgr: np.ndarray) -> Dict[str, float]:
    """Share of the frame in Haldi yellow, Mehndi green, stage-light colours and fire glow, plus brightness."""
    small = cv2.resize(frame_bgr, (160, 90))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    sat = (S > 90) & (V > 60)
    n = float(H.size)
    return {
        "brightness": round(float(V.mean()) / 255.0, 3),
        "yellow": round(float(np.count_nonzero(sat & (H >= 18) & (H <= 34))) / n, 3),
        "green": round(float(np.count_nonzero(sat & (H >= 36) & (H <= 85))) / n, 3),
        "stage": round(float(np.count_nonzero((S > 100) & (V > 80) & (H >= 110) & (H <= 170))) / n, 3),
        "fire": round(float(np.count_nonzero((H >= 5) & (H <= 20) & (S > 150) & (V > 200))) / n, 3),
    }


MOMENT_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("getting_ready", ["getting ready", "makeup", "make up", "tayari", "bride ready", "groom ready", "dressing"]),
    ("haldi", ["haldi", "pithi", "ubtan"]),
    ("mehndi", ["mehndi", "mehendi", "henna"]),
    ("sangeet", ["sangeet", "cocktail", "dj night", "lady sangeet"]),
    ("engagement", ["engagement", "ring ceremony", "sagai", "roka", "mangni"]),
    ("tilak", ["tilak", "tika"]),
    ("chooda", ["chooda", "chura", "kalire"]),
    ("jaggo", ["jaggo"]),
    ("baraat", ["baraat", "barat", "ghodi", "procession", "milni"]),
    ("varmala", ["varmala", "jaimala", "jaymala", "garland"]),
    ("pheras", ["phera", "pheras", "phere", "mandap", "havan", "saat phere", "kanyadaan", "kanyadan"]),
    ("sindoor", ["sindoor", "mangalsutra"]),
    ("vidaai", ["vidaai", "vidai", "bidaai", "bidai", "doli"]),
    ("reception", ["reception"]),
    ("nikah", ["nikah", "nikkah"]),
    ("anand_karaj", ["anand karaj", "anand kaaraj", "lavan", "laavan", "gurudwara"]),
    ("muhurtham", ["muhurtham", "muhurtam", "thali", "kalyanam"]),
    ("couple_shoot", ["pre wedding", "prewedding", "couple shoot", "post wedding", "save the date"]),
    ("decor", ["decor", "decoration", "venue", "details"]),
    ("drone", ["drone", "dji", "aerial", "mavic"]),
]
_KW_RE = [(m, re.compile(r"(^|[^a-z])(" + "|".join(re.escape(k) for k in kws) + r")([^a-z]|$)")) for m, kws in MOMENT_KEYWORDS]


def moment_from_path(path: str) -> Optional[str]:
    """Ritual from the folder / file name (the editor's own sorting is the strongest hint).
    The deepest matching folder wins, so 'Wedding/Haldi/C001.mp4' is Haldi."""
    parts = re.split(r"[\\/]+", path.lower())
    for part in reversed(parts):
        text = re.sub(r"[_\-.]+", " ", part)
        for moment, rx in _KW_RE:
            if rx.search(text):
                return moment
    return None


def moment_from_visuals(cues: Dict[str, float], face_count: int, camera_move: str) -> Tuple[Optional[str], float]:
    """Best guess from light and colour only, with a confidence 0-1 (heuristic)."""
    night = cues.get("brightness", 0.5) < 0.33
    if cues.get("yellow", 0) >= 0.22 and not night:
        return "haldi", min(0.8, 0.45 + cues["yellow"])
    if cues.get("fire", 0) >= 0.004 and night:
        return "pheras", 0.5
    if cues.get("green", 0) >= 0.22 and not night:
        return "mehndi", min(0.7, 0.4 + cues["green"])
    if night and cues.get("stage", 0) >= 0.12:
        return "sangeet", 0.45
    if night and face_count >= 4 and camera_move in ("pan", "shaky", "orbit"):
        return "baraat", 0.35
    if face_count == 0 and camera_move in ("drone",):
        return "drone", 0.7
    if face_count == 0:
        return "decor", 0.3
    return None, 0.0


# ------------------------------------------------------------------ sound

def audio_profile(path: str, start: float = 0.0, end: Optional[float] = None) -> Dict[str, Any]:
    """speech / music / crowd / silence / none for a clip, its loudness and its loudest moments.
    Heuristic: syllable-rate loudness changes (2-8 Hz) mean speech; noisy, flat spectra mean crowd."""
    try:
        from packages.ai_engine.audio_io import load_audio
        y, sr = load_audio(path, 16000)
    except Exception:
        return {"audio_type": "none", "loudness_db": None, "loudness_peaks": []}
    a = int(max(0.0, start) * sr)
    b = int(end * sr) if end else len(y)
    seg = y[a:b] if b > a else y
    return classify_audio(seg, sr, offset=start)


def classify_audio(y: np.ndarray, sr: int, offset: float = 0.0) -> Dict[str, Any]:
    if y.size < sr // 2:
        return {"audio_type": "none", "loudness_db": None, "loudness_peaks": []}
    y = y.astype(np.float32)
    overall = float(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9))
    hop = sr // 50  # 20 ms
    n = y.size // hop
    env = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9
    if overall < -48:
        kind = "silence"
    else:
        # loudness modulation spectrum: speech pulses at the syllable rate
        e = env / env.max()
        e = e - e.mean()
        spec = np.abs(np.fft.rfft(e)) ** 2
        freqs = np.fft.rfftfreq(e.size, d=1.0 / 50)
        band = spec[(freqs >= 2) & (freqs <= 8)].sum()
        total = spec[(freqs >= 0.5) & (freqs <= 20)].sum() + 1e-9
        modulation = float(band / total)
        # spectral flatness: ~1 for noise (crowd, cheering), low for tones (music, voice)
        frame = 1024
        k = max(1, min(200, y.size // frame))
        idx = np.linspace(0, y.size - frame, k).astype(int)
        mags = np.abs(np.fft.rfft(np.stack([y[i:i + frame] * np.hanning(frame) for i in idx]), axis=1)) + 1e-9
        flat = float(np.median(np.exp(np.mean(np.log(mags), axis=1)) / np.mean(mags, axis=1)))
        dips = float(np.mean(env < 0.25 * np.median(env)))   # pauses between words
        if flat >= 0.35:
            kind = "crowd"
        elif modulation >= 0.35 and dips >= 0.05:
            kind = "speech"
        else:
            kind = "music"
    # loudest moments, at least 1 s apart
    smooth = np.convolve(env, np.ones(10) / 10, mode="same")
    thr = np.percentile(smooth, 90)
    peaks: List[float] = []
    for i in np.argsort(smooth)[::-1]:
        if smooth[i] < thr or len(peaks) >= 5:
            break
        t = offset + i / 50.0
        if all(abs(t - p) >= 1.0 for p in peaks):
            peaks.append(round(t, 2))
    return {"audio_type": kind, "loudness_db": round(overall, 1), "loudness_peaks": sorted(peaks)}


# ------------------------------------------------------------------ putting it together

def wedding_score(quality: float, faces: List[Dict[str, Any]], face_count: int, shot_type: str, camera_move: str) -> float:
    """0-100: picture quality + face focus + eyes open + smile + how clearly a person is the hero."""
    q = float(quality or 0)
    if faces:
        f = faces[0]
        hero = _clamp01(f["box"][3] / 0.25)
        if face_count > 6:
            hero *= 0.7          # crowd shot: nobody is the hero
        score = 0.40 * q + 0.20 * f["sharpness"] + 15 * f["eyes_open"] + 10 * f["smile"] + 15 * hero
    else:
        score = 0.80 * q + (8 if shot_type == "detail" else 4)
    if camera_move == "shaky":
        score -= 15
    elif camera_move in ("push_in", "orbit", "drone"):
        score += 5
    return round(max(0.0, min(100.0, score)), 1)


def analysis_frames(result: Dict[str, Any]) -> List[np.ndarray]:
    """The frames the wedding analysis looks at: start, peak and end of the chosen part of the clip
    (the image itself for a still). People recognition uses the same frames."""
    path = result["file_path"]
    frames: List[np.ndarray] = []
    if result.get("is_still"):
        try:
            from PIL import Image
            with Image.open(path) as im:
                frames.append(cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR))
        except Exception:
            pass
        return frames
    in_f, out_f = int(result.get("in_frame", 0)), int(result.get("out_frame", 0))
    peak = int(result.get("action_peak_frame", (in_f + out_f) // 2))
    cap = cv2.VideoCapture(path)
    try:
        span = max(1, out_f - in_f)
        for f in sorted({in_f + span // 6, peak, out_f - span // 6}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, f))
            ok, img = cap.read()
            if ok and img is not None:
                frames.append(img)
    finally:
        cap.release()
    return frames


def enrich_wedding_fields(result: Dict[str, Any], motion: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Adds faces, shot_type (from face size), camera_move, audio_type, moment and wedding_score."""
    path = result["file_path"]
    in_f, out_f = int(result.get("in_frame", 0)), int(result.get("out_frame", 0))
    frames = analysis_frames(result)

    best_faces: List[Dict[str, Any]] = []
    count = 0
    cues: Dict[str, float] = {}
    bokeh = 0.0
    for i, img in enumerate(frames):
        faces = detect_faces(img)
        count = max(count, len(faces))
        if faces and (not best_faces or faces[0]["box"][3] * faces[0]["confidence"] > best_faces[0]["box"][3] * best_faces[0]["confidence"]):
            best_faces = faces
        if i == len(frames) // 2:
            cues = visual_cues(img)
            bokeh = _bokeh_ratio(cv2.cvtColor(cv2.resize(img, (320, 180)), cv2.COLOR_BGR2GRAY))

    biggest = best_faces[0]["box"][3] if best_faces else 0.0
    if not result.get("is_still"):
        result["shot_type"] = shot_type_from_faces(biggest, count, bokeh)
        result["camera_move"] = classify_camera_move(motion, path, in_f, out_f)
        result.update(audio_profile(path))
    else:
        result["camera_move"] = "static"
        result.update({"audio_type": "none", "loudness_db": None, "loudness_peaks": []})
    result["faces"] = {
        "count": count,
        "biggest": round(biggest, 3),
        "sharpness": best_faces[0]["sharpness"] if best_faces else None,
        "eyes_open": best_faces[0]["eyes_open"] if best_faces else None,
        "smile": best_faces[0]["smile"] if best_faces else None,
        "boxes": [f["box"] for f in best_faces[:6]],
    }
    result["visual_cues"] = cues
    folder_moment = moment_from_path(path)
    if folder_moment:
        result["moment"], result["moment_source"], result["moment_confidence"] = folder_moment, "folder", 0.95
    else:
        m, conf = moment_from_visuals(cues, count, result["camera_move"])
        result["moment"], result["moment_source"], result["moment_confidence"] = m, ("visual" if m else "unknown"), round(conf, 2)
    result["wedding_score"] = wedding_score(result.get("quality_score", 0), best_faces, count,
                                            result.get("shot_type", ""), result["camera_move"])
    result["wedding_version"] = WEDDING_VERSION
    return result


def label_moments(analyzed: List[Dict[str, Any]], gap_minutes: float = 25.0) -> int:
    """Clips shot close together belong to the same event. Unknown or weak (visual-only) moments take
    the event's most common confident moment. Returns how many clips were relabelled."""
    timed = [c for c in analyzed if c.get("abs_start") or c.get("rec_start")]
    timed.sort(key=lambda c: float(c.get("abs_start") or c.get("rec_start") or 0))
    events: List[List[Dict[str, Any]]] = []
    last = None
    for c in timed:
        t = float(c.get("abs_start") or c.get("rec_start") or 0)
        if last is None or t - last > gap_minutes * 60:
            events.append([])
        events[-1].append(c)
        last = t + float(c.get("duration") or 0)
    changed = 0
    for eid, ev in enumerate(events):
        votes: Dict[str, float] = {}
        for c in ev:
            c["event_id"] = eid
            m = c.get("moment")
            if m and m not in ("decor", "drone", "couple_shoot") and float(c.get("moment_confidence") or 0) >= 0.45:
                votes[m] = votes.get(m, 0.0) + float(c.get("moment_confidence") or 0)
        if not votes:
            continue
        top = max(votes, key=votes.get)
        for c in ev:
            if c.get("moment_source") in ("unknown", "visual") and float(c.get("moment_confidence") or 0) < 0.6 and c.get("moment") != top:
                if c.get("moment") in ("decor", "drone") and c.get("moment_source") == "visual":
                    continue  # B-roll stays B-roll
                c["moment"], c["moment_source"], c["moment_confidence"] = top, "event", 0.5
                changed += 1
    return changed


# ------------------------------------------------------------------ shot tiers (STEP 3) and score bookkeeping

TIER_BONUS = {"A": 10.0, "B": 0.0, "C": -25.0}


def recompute_scores(clip: Dict[str, Any]) -> None:
    """quality_score / wedding_score = analysis value + people bonus (STEP 2) + tier bonus (STEP 3).
    The analysis values are kept in *_base, so any step can be re-run without adding up."""
    bonus = float(clip.get("people_bonus") or 0) + TIER_BONUS.get(clip.get("tier") or "B", 0.0)
    for key in ("quality_score", "wedding_score"):
        if key in clip:
            clip.setdefault(f"{key}_base", clip[key])
            clip[key] = round(max(0.0, min(100.0, float(clip[f"{key}_base"]) + bonus)), 1)


def local_tier(clip: Dict[str, Any]) -> Tuple[str, str]:
    """A / B / C without the cloud, from the STEP 1 analysis, with a short reason.
    A = hero shot, B = usable, C = reject (shaky, eyes closed in a close-up, or poor overall)."""
    if clip.get("is_still"):
        return "B", "graphic / photo"
    ws = float(clip.get("wedding_score_base", clip.get("wedding_score", clip.get("quality_score", 50))) or 0)
    faces = clip.get("faces") or {}
    move = clip.get("camera_move", "")
    close = clip.get("shot_type") in ("close_up", "mid")
    eyes = faces.get("eyes_open")
    if move == "shaky" and ws < 60:
        return "C", "shaky camera"
    if close and faces.get("count") and eyes is not None and eyes < 0.2 and (faces.get("biggest") or 0) >= 0.2:
        return "C", "eyes look closed"
    if ws < 38:
        return "C", "low quality"
    if ws >= 74 and move != "shaky" and (not faces.get("count") or (eyes or 0) >= 0.5 or not close):
        return "A", "sharp, steady and well framed"
    return "B", "usable"


def assign_local_tiers(analyzed: List[Dict[str, Any]]) -> Dict[str, int]:
    """Gives every clip without a Smart AI review a local tier. Returns the count per tier."""
    counts = {"A": 0, "B": 0, "C": 0}
    for c in analyzed:
        if c.get("tier_source") != "ai":
            c["tier"], c["tier_note"] = local_tier(c)
            c["tier_source"] = "local"
        recompute_scores(c)
        counts[c["tier"]] = counts.get(c["tier"], 0) + 1
    return counts


def contact_sheet(clip: Dict[str, Any], frames: int = 6, cell: Optional[Tuple[int, int]] = None) -> Tuple[Optional[bytes], List[float]]:
    """A JPEG grid of `frames` frames spread over the usable part of the clip, each stamped with its time
    in seconds (from the clip start), for Smart AI to review the whole shot instead of one frame.
    Landscape clips: 3 x 2 cells of 320x240. Vertical (phone / reel) clips: one row of 200x356 cells."""
    path = clip["file_path"]
    if cell is None:
        probe = cv2.VideoCapture(path)
        vw, vh = probe.get(cv2.CAP_PROP_FRAME_WIDTH) or 16, probe.get(cv2.CAP_PROP_FRAME_HEIGHT) or 9
        probe.release()
        cell = (200, 356) if vh > vw * 1.1 else (320, 240)
    fps = float(clip.get("native_fps") or 25.0)
    dur = float(clip.get("duration") or 0)
    a = float(clip.get("full_in_seconds", clip.get("in_seconds", 0)) or 0)
    b = float(clip.get("full_out_seconds", clip.get("out_seconds", dur)) or dur)
    if b <= a:
        a, b = 0.0, max(dur, 0.1)
    times = [round(a + (b - a) * (k + 0.5) / frames, 2) for k in range(frames)]
    cols = frames if cell[1] > cell[0] else 3
    rows = int(math.ceil(frames / cols))
    cw, ch = cell
    sheet = np.zeros((rows * ch, cols * cw, 3), dtype=np.uint8)
    got = 0
    cap = cv2.VideoCapture(path)
    try:
        for k, t in enumerate(times):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
            ok, img = cap.read()
            if not ok or img is None:
                continue
            h, w = img.shape[:2]
            s = min(cw / w, ch / h)
            small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))))
            y0 = (k // cols) * ch + (ch - small.shape[0]) // 2
            x0 = (k % cols) * cw + (cw - small.shape[1]) // 2
            sheet[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
            label = f"{t:.1f}s"
            cv2.putText(sheet, label, ((k % cols) * cw + 6, (k // cols) * ch + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
            cv2.putText(sheet, label, ((k % cols) * cw + 6, (k // cols) * ch + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            got += 1
    finally:
        cap.release()
    if not got:
        return None, times
    ok, buf = cv2.imencode(".jpg", sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    return (buf.tobytes() if ok else None), times


def review_cache_key(clip: Dict[str, Any]) -> Optional[str]:
    key = _content_key(clip["file_path"])
    if not key:
        return None
    return hashlib.sha1(f"{key}|{clip.get('full_in_seconds')}|{clip.get('full_out_seconds')}".encode()).hexdigest()


# Global instance
in_clip_trimmer = InClipTrimmer()
