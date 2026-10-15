"""
AI Video Flow - Audio Beat & Energy Analyzer
Detects BPM (tempo), beat timestamps, onset transients, and music energy drops
for rhythm-synchronized video editing.
"""

import os
import logging
from typing import Dict, Any, List

logger = logging.getLogger("ai_video_flow.beat_analyzer")


class BeatAnalyzer:
    """
    Extracts beat grids, tempo, and energy peaks from music tracks.
    """

    def analyze_audio(self, audio_path: str, target_fps: float = 25.0) -> Dict[str, Any]:
        """
        Analyzes an audio file and returns beat markers and energy sections.
        Uses librosa if available; otherwise falls back to a deterministic rhythm estimator.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            import librosa
            return self._analyze_with_librosa(audio_path, target_fps)
        except ImportError:
            logger.warning("Librosa not installed. Using fallback beat estimator.")
            return self._analyze_fallback(audio_path, target_fps)

    def _analyze_with_librosa(self, audio_path: str, target_fps: float) -> Dict[str, Any]:
        """Full spectral and beat tracking using Librosa."""
        import librosa
        import numpy as np

        y, sr = librosa.load(audio_path, sr=22050)
        duration = float(librosa.get_duration(y=y, sr=sr))

        # 1. Tempo & Beat frames
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        if isinstance(tempo, np.ndarray):
            tempo = float(tempo[0])
        else:
            tempo = float(tempo)

        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        # 2. Onset strength (transients / kicks / snares)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        
        # 3. Detect major drops/peaks (top 15% energy frames)
        threshold = np.percentile(onset_env, 85)
        peak_frames = np.where(onset_env > threshold)[0]
        peak_times = librosa.frames_to_time(peak_frames, sr=sr).tolist()

        # Convert beat times to timeline frame numbers
        timeline_beat_frames = [int(round(t * target_fps)) for t in beat_times]

        return {
            "file_path": audio_path,
            "duration": round(duration, 2),
            "bpm": round(tempo, 1),
            "beat_count": len(beat_times),
            "beat_times": [round(t, 3) for t in beat_times],
            "beat_timeline_frames": timeline_beat_frames,
            "drop_times": [round(t, 2) for t in peak_times[:20]],
            "engine": "Librosa Full Precision"
        }

    def _analyze_fallback(self, audio_path: str, target_fps: float) -> Dict[str, Any]:
        """Fallback when Librosa is not yet installed: estimated standard 120 BPM."""
        file_size = os.path.getsize(audio_path)
        # Approximate duration estimation if needed or assume 3.5 min
        estimated_duration = 180.0
        bpm = 120.0
        interval = 60.0 / bpm  # 0.5 sec per beat

        beat_times = []
        curr = 0.5
        while curr < estimated_duration:
            beat_times.append(curr)
            curr += interval

        timeline_frames = [int(round(t * target_fps)) for t in beat_times]

        return {
            "file_path": audio_path,
            "duration": estimated_duration,
            "bpm": bpm,
            "beat_count": len(beat_times),
            "beat_times": [round(t, 2) for t in beat_times],
            "beat_timeline_frames": timeline_frames,
            "drop_times": [30.0, 60.0, 90.0, 120.0],
            "engine": "Standard Beat Grid Estimator (Install librosa for deep spectral beats)"
        }


# Global instance
beat_analyzer = BeatAnalyzer()
