"""
AI Video Flow - Automated Scene & Shot Splitter
Powered by PySceneDetect and OpenCV Adaptive Analysis.
Scans long continuous raw wedding video footage and extracts individual
shots, cuts, and transitions for rapid timeline assembly.
"""

import os
import cv2
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.scene_detector")


class SceneDetectorEngine:
    """
    Detects scene cuts, fast transitions, and camera movement breaks in continuous video files.
    """

    def detect_scenes(
        self,
        video_path: str,
        threshold: float = 27.0,
        min_scene_len_sec: float = 1.5
    ) -> Dict[str, Any]:
        """
        Scans video file and returns a structured list of scenes with exact timecodes.
        Uses PySceneDetect if installed, with native OpenCV fallback.
        """
        if not os.path.exists(video_path):
            return {"success": False, "error": f"Video file not found: {video_path}"}

        try:
            from scenedetect import open_video, SceneManager
            from scenedetect.detectors import ContentDetector

            video = open_video(video_path)
            fps = video.frame_rate
            scene_manager = SceneManager()
            min_scene_frames = int(fps * min_scene_len_sec)
            scene_manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_frames))

            scene_manager.detect_scenes(video, show_progress=False)
            scene_list = scene_manager.get_scene_list()

            results = []
            for idx, (start_time, end_time) in enumerate(scene_list):
                results.append({
                    "scene_index": idx + 1,
                    "start_sec": start_time.get_seconds(),
                    "end_sec": end_time.get_seconds(),
                    "start_timecode": start_time.get_timecode(),
                    "end_timecode": end_time.get_timecode(),
                    "start_frame": start_time.get_frames(),
                    "end_frame": end_time.get_frames(),
                    "duration_sec": round(end_time.get_seconds() - start_time.get_seconds(), 2)
                })

            return {
                "success": True,
                "video_path": video_path,
                "fps": fps,
                "total_scenes_detected": len(results),
                "scenes": results
            }

        except Exception as e:
            logger.warning(f"PySceneDetect failed, using OpenCV native fallback: {e}")
            return self._fallback_opencv_detect(video_path, threshold, min_scene_len_sec)

    def _fallback_opencv_detect(
        self,
        video_path: str,
        threshold: float,
        min_scene_len_sec: float
    ) -> Dict[str, Any]:
        """OpenCV histogram diff fallback scene detector."""
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        min_frames = int(fps * min_scene_len_sec)

        prev_hist = None
        cuts = [0]
        frame_idx = 0

        # Sample every 3 frames for high speed
        step = 3
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % step == 0:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
                cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

                if prev_hist is not None:
                    diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
                    if diff > (threshold * 1.5) and (frame_idx - cuts[-1]) >= min_frames:
                        cuts.append(frame_idx)
                prev_hist = hist

            frame_idx += 1

        cap.release()
        cuts.append(frame_idx)

        scenes = []
        for i in range(len(cuts) - 1):
            s_f = cuts[i]
            e_f = cuts[i + 1]
            s_sec = round(s_f / fps, 2)
            e_sec = round(e_f / fps, 2)
            scenes.append({
                "scene_index": i + 1,
                "start_sec": s_sec,
                "end_sec": e_sec,
                "start_frame": s_f,
                "end_frame": e_f,
                "duration_sec": round(e_sec - s_sec, 2)
            })

        return {
            "success": True,
            "video_path": video_path,
            "fps": fps,
            "total_scenes_detected": len(scenes),
            "scenes": scenes,
            "engine": "opencv_fallback"
        }


scene_detector_engine = SceneDetectorEngine()
