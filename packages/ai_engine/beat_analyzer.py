"""
AI Video Flow - Audio Beat & Energy Analyzer
Detects BPM (tempo), beat timestamps, onset transients, and music energy drops
for rhythm-synchronized video editing.
"""

import os
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ai_video_flow.beat_analyzer")


def find_energy_drops(times: List[float], energy: List[float], beat_times: Optional[List[float]] = None,
                      window: float = 2.0, min_gap: float = 8.0, max_drops: int = 12) -> List[float]:
    """Moments where the song suddenly gets louder (a drop / chorus hit), spread across the song.

    times / energy: a loudness curve (seconds, any scale). For every point it compares the average
    loudness of the next `window` seconds with the previous `window` seconds; the biggest rises that
    are local maxima become drops, at least `min_gap` seconds apart, snapped to the nearest beat."""
    import numpy as np
    t = np.asarray(times, dtype=float)
    e = np.asarray(energy, dtype=float)
    if t.size < 4 or e.size != t.size:
        return []
    step = float(np.median(np.diff(t))) or 0.05
    k = max(1, int(round(window / step)))
    if t.size <= 2 * k:
        return []
    c = np.concatenate([[0.0], np.cumsum(e)])
    idx = np.arange(k, t.size - k)
    before = (c[idx] - c[idx - k]) / k
    after = (c[idx + k] - c[idx]) / k
    rise = after - before
    # only clear jumps: well above the typical change in this song
    level = float(np.percentile(rise, 90))
    spread = float(np.std(rise)) or 1e-9
    cand = [int(i) for j, i in enumerate(idx)
            if rise[j] > 0 and rise[j] >= level and rise[j] >= 0.5 * spread
            and rise[j] == rise[max(0, j - k):j + k + 1].max()]
    cand.sort(key=lambda i: rise[i - k], reverse=True)
    picked: List[float] = []
    for i in cand:
        ti = float(t[i])
        if all(abs(ti - p) >= min_gap for p in picked):
            picked.append(ti)
        if len(picked) >= max_drops:
            break
    if beat_times:
        beats = np.asarray(sorted(beat_times), dtype=float)
        picked = [float(beats[np.abs(beats - p).argmin()]) if np.abs(beats - p).min() <= 0.6 else p for p in picked]
    return sorted(round(p, 2) for p in picked)


def pick_downbeats(beat_times: List[float], strength: List[float], beats_per_bar: int = 4) -> List[float]:
    """Downbeats (beat 1 of every bar): the bar phase whose beats are strongest on average
    (kick / bass hits usually fall on beat 1). strength = onset or low-band energy at each beat."""
    if len(beat_times) < beats_per_bar * 2 or len(strength) != len(beat_times):
        return list(beat_times[::beats_per_bar])
    import numpy as np
    st = np.asarray(strength, dtype=float)
    phase = int(np.argmax([st[k::beats_per_bar].mean() for k in range(beats_per_bar)]))
    return [round(float(t), 3) for t in beat_times[phase::beats_per_bar]]


def label_sections(bounds: List[float], energies: List[float], drops: List[float], duration: float) -> List[Dict[str, Any]]:
    """Names song sections from their loudness: intro / verse / chorus / drop / bridge / outro.
    bounds = section start times (first is 0), energies = mean loudness per section (any scale)."""
    import numpy as np
    if not bounds:
        return []
    e = np.asarray(energies, dtype=float)
    rel = e / (e.max() or 1.0)
    ends = list(bounds[1:]) + [duration]
    out = []
    for i, (a, b) in enumerate(zip(bounds, ends)):
        r = float(rel[i])
        hit = any(abs(d - a) <= 2.5 for d in drops)
        if i == 0 and r < 0.75 and len(bounds) > 1:
            label = "intro"
        elif i == len(bounds) - 1 and r < 0.75 and len(bounds) > 2:
            label = "outro"
        elif r >= 0.85 and hit:
            label = "drop"
        elif r >= 0.8:
            label = "chorus"
        elif 0 < i < len(bounds) - 1 and out and out[-1]["label"] in ("chorus", "drop") and r < 0.7:
            label = "bridge"
        else:
            label = "verse"
        out.append({"start": round(float(a), 2), "end": round(float(b), 2), "label": label, "energy": round(r, 2)})
    return out


def regions_from_score(times: List[float], score: List[float], duration: float, min_len: float = 1.5) -> Tuple[List[List[float]], List[List[float]]]:
    """(vocal regions, instrumental regions) from a per-frame vocal score: above the song's own middle level
    = vocal. Regions shorter than min_len are merged away; instrumental = everything else (>= 2 s)."""
    import numpy as np
    t = np.asarray(times, dtype=float)
    v = np.asarray(score, dtype=float)
    if t.size < 4:
        return [], [[0.0, round(duration, 2)]] if duration > 0 else []
    step = float(np.median(np.diff(t))) or 0.05
    k = max(1, int(round(1.0 / step)))
    sm = np.convolve(v, np.ones(k) / k, mode="same")
    thr = float(np.percentile(sm, 50) + 0.35 * np.std(sm))
    on = sm > thr
    vocal: List[List[float]] = []
    start = None
    for i, flag in enumerate(on):
        if flag and start is None:
            start = t[i]
        if (not flag or i == len(on) - 1) and start is not None:
            end = t[i]
            if end - start >= min_len:
                if vocal and start - vocal[-1][1] < 1.0:
                    vocal[-1][1] = round(float(end), 2)
                else:
                    vocal.append([round(float(start), 2), round(float(end), 2)])
            start = None
    inst: List[List[float]] = []
    cur = 0.0
    for a, b in vocal + [[duration, duration]]:
        if a - cur >= 2.0:
            inst.append([round(cur, 2), round(a, 2)])
        cur = max(cur, b)
    return vocal, inst


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

        try:
            y, sr = librosa.load(audio_path, sr=22050)
        except Exception:
            # m4a/aac and video files: decode with ffmpeg instead
            from packages.ai_engine.audio_io import load_audio
            y, sr = load_audio(audio_path, 22050)
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
        
        # 3. Drops: real loudness rises across the song (not just the loudest onsets at the start)
        rms = librosa.feature.rms(y=y)[0]
        rms_times = librosa.frames_to_time(np.arange(rms.size), sr=sr).tolist()
        drop_times = find_energy_drops(rms_times, rms.tolist(), beat_times)

        # Convert beat times to timeline frame numbers
        timeline_beat_frames = [int(round(t * target_fps)) for t in beat_times]

        result = {
            "file_path": audio_path,
            "duration": round(duration, 2),
            "bpm": round(tempo, 1),
            "beat_count": len(beat_times),
            "beat_times": [round(t, 3) for t in beat_times],
            "beat_timeline_frames": timeline_beat_frames,
            "drop_times": drop_times,
            "engine": "Librosa Full Precision"
        }
        try:
            result.update(self._song_structure(y, sr, beat_frames, beat_times, onset_env, rms, drop_times, duration))
        except Exception as e:  # the beats above are enough for every planner; structure is a bonus
            logger.warning(f"Song structure skipped: {e}")
        return result

    @staticmethod
    def _song_structure(y, sr, beat_frames, beat_times, onset_env, rms, drop_times, duration) -> Dict[str, Any]:
        """Bars, sections, vocal / instrumental parts and the audible end of the song."""
        import librosa
        import numpy as np
        out: Dict[str, Any] = {}
        # 1. Downbeats / bars
        strength = [float(onset_env[min(len(onset_env) - 1, int(f))]) for f in beat_frames]
        downbeats = pick_downbeats(beat_times, strength)
        out["downbeat_times"] = downbeats
        out["bar_seconds"] = round(float(np.median(np.diff(downbeats))), 3) if len(downbeats) > 2 else None
        # 2. Sections: group similar bars (harmony + timbre), boundaries on downbeats
        if len(beat_frames) >= 16:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
            feat = np.vstack([librosa.util.normalize(chroma, axis=0), librosa.util.normalize(mfcc, axis=1)])
            sync = librosa.util.sync(feat, beat_frames, aggregate=np.median)
            k = int(max(3, min(10, round(duration / 20.0))))
            k = min(k, max(2, sync.shape[1] // 4))
            seg = librosa.segment.agglomerative(sync, k)
            seg_beats = [int(b) for b in seg]
            starts = sorted({0.0} | {float(beat_times[min(len(beat_times) - 1, b)]) for b in seg_beats if b > 0})
            if downbeats:
                db = np.asarray(downbeats)
                starts = sorted({0.0} | {float(db[np.abs(db - s_).argmin()]) for s_ in starts if s_ > 0})
            rms_t = librosa.frames_to_time(np.arange(rms.size), sr=sr)
            ends = starts[1:] + [duration]
            energies = [float(rms[(rms_t >= a) & (rms_t < b)].mean()) if np.any((rms_t >= a) & (rms_t < b)) else 0.0
                        for a, b in zip(starts, ends)]
            out["sections"] = label_sections(starts, energies, drop_times, duration)
        # 3. Vocal vs instrumental (heuristic): harmonic energy in the voice band, tonal not noisy
        harmonic = librosa.effects.harmonic(y)
        S = np.abs(librosa.stft(harmonic, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        band = S[(freqs >= 250) & (freqs <= 3500)].sum(axis=0) / (S.sum(axis=0) + 1e-9)
        flat = librosa.feature.spectral_flatness(S=S)[0]
        loud = librosa.feature.rms(S=S)[0]
        score = band * (1 - np.clip(flat * 5, 0, 1)) * (loud / (loud.max() or 1))
        times = librosa.frames_to_time(np.arange(score.size), sr=sr, hop_length=512)
        out["vocal_regions"], out["instrumental_regions"] = regions_from_score(times.tolist(), score.tolist(), duration)
        # 4. Where the song really ends (after this it is silence or a long tail)
        rms_t = librosa.frames_to_time(np.arange(rms.size), sr=sr)
        audible = np.where(rms > 0.05 * (rms.max() or 1))[0]
        out["audible_end"] = round(float(rms_t[audible[-1]]), 2) if audible.size else round(duration, 2)
        return out

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
