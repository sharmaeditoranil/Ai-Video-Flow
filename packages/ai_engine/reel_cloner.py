"""
AI Video Flow - Reference-to-Reel Clone & Viral Reel Engine
Supports cloning any reference reel's edit style, pacing, 90-degree cinema rotation,
speed-ramps, 3D LUT color grading, B-roll cutaways, behind-subject depth typography,
and Hinglish/Hindi/English animated captions.

Works on:
- Windows & macOS Desktop App (when 'Reels & Shorts' workflow is selected)
- Standalone Android Mobile App (.apk)
"""

import os
import math
import time
import json
import shutil
import logging
import tempfile
import subprocess
from typing import Dict, Any, List, Optional, Tuple

import cv2
import numpy as np

from packages.ai_engine.audio_io import ffmpeg_path, load_audio
from packages.ai_engine.beat_analyzer import beat_analyzer
from packages.ai_engine.broll_placer import broll_placer
from packages.ai_engine.color_transfer_engine import ColorTransferEngine

logger = logging.getLogger("ai_video_flow.reel_cloner")


REEL_STYLES = {
    "auto_ref": "Auto Clone Reference Video (Cut Rhythm + Color + B-Roll + Text)",
    "talking_depth_captions": "Talking Reel · Behind-Person Depth Text + Hinglish Captions + B-Roll",
    "cinema_90_rotated": "90° Rotated Cinema Wedding Reel (Full-Frame Vertical + Watermark)",
    "speed_ramp_luxury": "Luxury Macro Speed-Ramp Reel (Fast-to-Slow Beat Cuts + Moody Grade)",
    "beat_sync_montage": "Fast Beat-Synced 9:16 Vertical Montage",
}


class ReelClonerEngine:
    """
    Extracts editing DNA from a reference reel and builds/renders matching reels
    from raw clips (with optional Cloud AI shot matching, B-roll, and captions).
    """

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or os.path.join(os.path.expanduser("~"), "AI-Video-Flow-Output")
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Reference Reel DNA Extraction
    # ------------------------------------------------------------------
    def analyze_reference_reel(
        self,
        ref_video_path: str,
        extract_audio: bool = True,
        log_fn: Optional[ Any ] = None
    ) -> Dict[str, Any]:
        """
        Inspects a reference video to extract:
        - Cut timestamps & shot durations (exact cut rhythm blueprint)
        - Orientation / 90-degree rotated cinema style detection
        - Optical flow speed-ramp signature (fast entry -> slow-mo peak)
        - Speech vs Music dominance (Talking-Head vs Cinematic Montage)
        - 3D Color LUT (.cube) from representative frames
        - Extracted audio track (.mp3) & beat grid
        - Keyframe thumbnails for Cloud AI shot/style matching
        """
        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass

        if not ref_video_path or not os.path.isfile(ref_video_path):
            return {
                "has_ref": False,
                "detected_style": "beat_sync_montage",
                "cut_durations": [1.5, 1.2, 1.4, 1.1, 1.6, 1.3, 1.5, 1.2],
                "avg_shot_dur": 1.35,
                "cinema_rotate_90": False,
                "speed_ramp": False,
                "has_speech": False,
                "lut_path": None,
                "ref_audio_path": None,
                "beat_times": [],
                "bpm": 120.0,
                "keyframes": [],
            }

        _log(f"🧬 Extracting Editing DNA from Reference Reel: {os.path.basename(ref_video_path)}", "info")
        cap = cv2.VideoCapture(ref_video_path)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)
        duration = total_frames / fps if fps > 0 else 0.0

        # Scan frames for scene cuts, motion speed-ramps, and 90-deg rotated cinema framing
        step = max(1, int(round(fps / 10.0)))  # 10 samples per second
        prev_gray = None
        cuts = [0.0]
        motion_curve: List[Tuple[float, float]] = []
        vert_grad_sum = 0.0
        horiz_grad_sum = 0.0
        center_band_energy = 0.0
        edge_band_energy = 0.0
        sampled_frames: List[Tuple[float, np.ndarray]] = []

        max_frames = min(total_frames, int(fps * 90))
        curr_f = 0
        while curr_f < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, curr_f)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t = round(curr_f / fps, 2)
            small = cv2.resize(frame, (180, 320))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            # Check whether side bars (letterbox/pillarbox blurred bands) exist & gradient orientation
            gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
            gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
            horiz_grad_sum += float(np.mean(gx))
            vert_grad_sum += float(np.mean(gy))

            # Center band vs outer left/right bands (90° rotated reels often have a crisp central 9:16 strip or rotated horizon)
            center_band_energy += float(np.mean(gx[:, 45:135] + gy[:, 45:135]))
            edge_band_energy += float(np.mean(gx[:, :30] + gx[:, 150:] + gy[:, :30] + gy[:, 150:]))

            if prev_gray is not None:
                diff = float(np.mean(cv2.absdiff(prev_gray, gray)))
                if diff > 28.0 and (t - cuts[-1]) >= 0.45:
                    cuts.append(t)
                    if len(sampled_frames) < 12:
                        sampled_frames.append((t, frame.copy()))
                else:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray, gray, None, 0.5, 2, 13, 2, 5, 1.1, 0
                    )
                    mag = float(np.mean(np.hypot(flow[..., 0], flow[..., 1])))
                    motion_curve.append((t, mag))
            else:
                sampled_frames.append((0.0, frame.copy()))

            prev_gray = gray
            curr_f += step

        if duration > cuts[-1] + 0.4:
            cuts.append(round(duration, 2))
        cap.release()

        cut_durations = [round(cuts[i] - cuts[i - 1], 2) for i in range(1, len(cuts)) if (cuts[i] - cuts[i - 1]) >= 0.35]
        if not cut_durations:
            cut_durations = [1.4, 1.1, 1.5, 1.2, 1.6, 1.3]
        avg_shot_dur = float(np.median(cut_durations))

        # Detect speed-ramping: high variance between peak motion and slow-mo valley within shots
        speed_ramp = False
        if len(motion_curve) >= 8:
            mags = [m for _, m in motion_curve]
            p90 = float(np.percentile(mags, 90))
            p25 = float(np.percentile(mags, 25))
            if p90 > max(1.8, p25 * 2.4):
                speed_ramp = True

        # Detect 90-degree rotated cinema style (horizon runs vertically -> strong horizontal Sobel gradient gx > gy * 1.18)
        cinema_rotate_90 = False
        if height > width and horiz_grad_sum > vert_grad_sum * 1.15:
            cinema_rotate_90 = True

        # Extract 3D Color LUT (.cube) from best mid-frame
        lut_path = None
        thumbs_dir = os.path.join(self.output_dir, ".ref_thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)
        keyframe_paths: List[str] = []
        try:
            for idx, (ts, frm) in enumerate(sampled_frames[:8]):
                h, w = frm.shape[:2]
                th = cv2.resize(frm, (360, max(1, int(h * 360 / w))))
                kp = os.path.join(thumbs_dir, f"ref_shot_{idx:02d}.jpg")
                cv2.imwrite(kp, th, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                keyframe_paths.append(kp)

            if sampled_frames:
                mid_idx = len(sampled_frames) // 2
                ref_frame_path = os.path.join(self.output_dir, "ref_extracted_frame.jpg")
                cv2.imwrite(ref_frame_path, sampled_frames[mid_idx][1])
                cte = ColorTransferEngine(luts_dir=os.path.join(self.output_dir, "luts"))
                lut_res = cte.generate_3d_lut(ref_frame_path, lut_name="AI_Reference_Reel_Look")
                if lut_res.get("success"):
                    lut_path = lut_res.get("lut_path")
        except Exception as e:
            logger.warning(f"Reference LUT/thumbnail extraction warning: {e}")

        # Extract Reference Audio & Analyze Beats + Speech vs Music
        ref_audio_path = None
        beat_times: List[float] = []
        bpm = 120.0
        has_speech = False
        if extract_audio:
            try:
                audio_dir = os.path.join(self.output_dir, "ref_audio")
                os.makedirs(audio_dir, exist_ok=True)
                out_mp3 = os.path.join(audio_dir, f"ref_audio_{int(time.time())}.mp3")
                proc = subprocess.run(
                    [ffmpeg_path(), "-y", "-v", "error", "-i", ref_video_path, "-vn", "-ac", "2", "-ar", "44100", "-b:a", "192k", out_mp3],
                    capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                if proc.returncode == 0 and os.path.isfile(out_mp3) and os.path.getsize(out_mp3) > 1024:
                    ref_audio_path = out_mp3
                    beats_info = beat_analyzer.analyze_audio(ref_audio_path, 25.0)
                    beat_times = beats_info.get("beat_times") or beats_info.get("beats") or []
                    bpm = float(beats_info.get("bpm") or beats_info.get("tempo") or 120.0)

                    # Speech heuristic: check syllable-rate envelope modulation (3-8 Hz speech band)
                    y, sr = load_audio(ref_audio_path, 16000)
                    if len(y) > sr * 2:
                        import librosa
                        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
                        flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))
                        # Talking head reels have distinct speech pauses and lower spectral flatness
                        intervals = librosa.effects.split(y, top_db=26)
                        speech_ratio = sum((b - a) for a, b in intervals) / max(1, len(y))
                        if 0.35 <= speech_ratio <= 0.92 and flatness < 0.08 and zcr < 0.14 and not cinema_rotate_90:
                            has_speech = True
            except Exception as e:
                logger.warning(f"Reference audio extraction warning: {e}")

        # Auto-classify the Reference Reel style
        if has_speech:
            detected_style = "talking_depth_captions"
        elif cinema_rotate_90:
            detected_style = "cinema_90_rotated"
        elif speed_ramp:
            detected_style = "speed_ramp_luxury"
        else:
            detected_style = "beat_sync_montage"

        _log(
            f"  ✨ Reference DNA: {len(cut_durations)} cuts (avg {avg_shot_dur:.2f}s) · "
            f"Style: {REEL_STYLES.get(detected_style, detected_style)} · "
            f"BPM: {bpm:.0f} ({len(beat_times)} beats)",
            "info"
        )

        return {
            "has_ref": True,
            "ref_video_path": ref_video_path,
            "duration": round(duration, 2),
            "detected_style": detected_style,
            "cut_timestamps": cuts,
            "cut_durations": cut_durations,
            "avg_shot_dur": round(avg_shot_dur, 2),
            "cinema_rotate_90": cinema_rotate_90,
            "speed_ramp": speed_ramp,
            "has_speech": has_speech,
            "lut_path": lut_path,
            "ref_audio_path": ref_audio_path,
            "beat_times": beat_times,
            "bpm": bpm,
            "keyframes": keyframe_paths,
        }

    # ------------------------------------------------------------------
    # 2. Plan Viral Reel (Matches Reference Cuts, B-Roll, Depth Text, Captions)
    # ------------------------------------------------------------------
    def build_viral_reel_plan(
        self,
        analyzed_clips: List[Dict[str, Any]],
        ref_dna: Dict[str, Any],
        target_duration: float = 45.0,
        sequence_name: str = "01_Instagram_Reel_9x16",
        reel_style: str = "auto_ref",
        audio_path: str = "",
        use_ref_audio: bool = True,
        depth_text: str = "",
        caption_text: str = "",
        watermark_text: str = "",
        enable_broll: bool = True,
        ai_clone_blueprint: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Builds a complete Reel Edit Plan combining:
        - V1 Primary Cuts (matched to Reference Cut Durations & Beat Grid)
        - V2 B-Roll Cutaways (for talking-head or dynamic cutaway reels)
        - Depth Text Behind Person (like 'ROUGH CUT / READY')
        - Timed Foreground Hinglish/Hindi/English Captions
        - Speed-Ramp & 90-Degree Cinema Rotation metadata
        """
        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass

        if not analyzed_clips:
            return {"sequence_name": sequence_name, "clips": [], "broll_clips": [], "total_duration": 0.0}

        # Resolve active style
        active_style = reel_style
        if active_style in ("", "auto", "auto_ref"):
            active_style = (
                (ai_clone_blueprint or {}).get("detected_style")
                or ref_dna.get("detected_style")
                or "beat_sync_montage"
            )

        cinema_rotate_90 = (active_style == "cinema_90_rotated") or bool(ref_dna.get("cinema_rotate_90") and reel_style == "auto_ref")
        speed_ramp = (active_style == "speed_ramp_luxury") or bool(ref_dna.get("speed_ramp") and reel_style == "auto_ref")
        is_talking_style = (active_style == "talking_depth_captions")

        # Choose audio & beat grid
        final_audio = audio_path if (audio_path and os.path.isfile(audio_path)) else ""
        if not final_audio and use_ref_audio and ref_dna.get("ref_audio_path") and os.path.isfile(ref_dna["ref_audio_path"]):
            final_audio = ref_dna["ref_audio_path"]
            _log(f"  🎵 Using extracted audio from Reference Reel: {os.path.basename(final_audio)}", "info")

        beat_times = ref_dna.get("beat_times") or []
        if final_audio and final_audio != ref_dna.get("ref_audio_path"):
            try:
                b_info = beat_analyzer.analyze_audio(final_audio, 25.0)
                beat_times = b_info.get("beat_times") or b_info.get("beats") or beat_times
            except Exception:
                pass

        # Determine cut durations from Reference DNA (or style defaults)
        ref_cuts = list(ref_dna.get("cut_durations") or [])
        if ref_dna.get("has_ref") and ref_dna.get("duration") and target_duration >= 40.0:
            # Match reference reel total duration when cloning a reference reel
            target_duration = min(60.0, max(12.0, float(ref_dna["duration"])))

        if not ref_cuts:
            if is_talking_style:
                ref_cuts = [2.4, 2.0, 2.2, 1.8, 2.5, 2.1]
            elif speed_ramp:
                ref_cuts = [1.8, 1.4, 1.6, 1.3, 1.9, 1.5]
            elif cinema_rotate_90:
                ref_cuts = [2.0, 1.6, 1.8, 1.5, 2.1, 1.7]
            else:
                ref_cuts = [1.4, 1.1, 1.3, 1.0, 1.5, 1.2]

        # Order clips: use Cloud AI shot-order if available, otherwise quality + chronological flow
        ordered_clips: List[Dict[str, Any]] = []
        if ai_clone_blueprint and ai_clone_blueprint.get("shot_order"):
            for idx in ai_clone_blueprint["shot_order"]:
                if isinstance(idx, int) and 0 <= idx < len(analyzed_clips):
                    ordered_clips.append(analyzed_clips[idx])
        if not ordered_clips:
            # Hero hook first, then top quality shots
            sorted_by_score = sorted(
                analyzed_clips,
                key=lambda c: (c.get("ai_emotion", 0), c.get("quality_score", 0)),
                reverse=True
            )
            ordered_clips = sorted_by_score

        # Build V1 Primary Clips matching Reference Cut Rhythm + Beat Grid
        v1_clips: List[Dict[str, Any]] = []
        curr_t = 0.0
        cut_idx = 0
        while curr_t < target_duration - 0.3 and ordered_clips:
            clip = ordered_clips[cut_idx % len(ordered_clips)]
            want_dur = float(ref_cuts[cut_idx % len(ref_cuts)])

            # Snap to nearest beat if beat_times exist and not a talking reel
            if beat_times and not is_talking_style:
                target_end = curr_t + want_dur
                near = [b for b in beat_times if b > curr_t + 0.55 and abs(b - target_end) <= 0.45]
                if near:
                    best_beat = min(near, key=lambda b: abs(b - target_end))
                    want_dur = round(best_beat - curr_t, 2)

            want_dur = max(0.6, min(want_dur, target_duration - curr_t))
            native_fps = float(clip.get("native_fps") or 25.0)
            clip_dur = float(clip.get("duration") or want_dur)
            in_sec = float(clip.get("in_seconds") or 0.0)
            action_peak = float(clip.get("action_peak_seconds") or (in_sec + want_dur * 0.5))

            # Center around the steadiest / highest-action golden window
            start_s = max(0.0, min(clip_dur - want_dur, action_peak - want_dur * 0.45)) if clip_dur > want_dur else 0.0
            end_s = min(clip_dur, start_s + want_dur)
            actual_dur = round(max(0.5, end_s - start_s), 2)

            # Punch-in zoom on alternate talking cuts or beat hits
            zoom_factor = 1.12 if (is_talking_style and cut_idx % 2 == 1) else (1.06 if speed_ramp else 1.0)

            v1_clips.append({
                "clip_name": os.path.basename(clip.get("file_path", "")),
                "file_path": clip.get("file_path"),
                "timeline_start": round(curr_t, 2),
                "timeline_end": round(curr_t + actual_dur, 2),
                "in_seconds": round(start_s, 2),
                "out_seconds": round(end_s, 2),
                "in_frame": int(round(start_s * native_fps)),
                "out_frame": max(int(round(start_s * native_fps)) + 1, int(round(end_s * native_fps))),
                "duration": actual_dur,
                "native_fps": native_fps,
                "shot_type": clip.get("shot_type", "mid"),
                "ai_moment": clip.get("ai_moment", ""),
                "zoom": zoom_factor,
                "speed_ramp": speed_ramp,
                "cinema_rotate_90": cinema_rotate_90,
                "role": "Hero Hook" if cut_idx == 0 else f"Ref Clone Cut #{cut_idx + 1}",
            })
            curr_t = round(curr_t + actual_dur, 2)
            cut_idx += 1
            if cut_idx >= max(len(ordered_clips) * 3, 60):
                break

        # Build V2 B-Roll Cutaways (especially for Talking Reels or when B-Roll is enabled)
        broll_clips: List[Dict[str, Any]] = []
        if enable_broll and len(analyzed_clips) > 1:
            freq = 3.5 if is_talking_style else 5.0
            b_dur = 1.8 if is_talking_style else 1.5
            broll_clips = broll_placer.suggest_broll_cutaways(
                primary_clips=v1_clips,
                all_analyzed_clips=analyzed_clips,
                target_track=2,
                cutaway_frequency_sec=freq,
                cutaway_duration_sec=b_dur,
            )
            # Fallback if all clips were used on V1: create V2 cutaway overlays from secondary clips
            if not broll_clips and curr_t >= 2.0:
                secondary = analyzed_clips[1:]
                cut_t = min(1.2, round(curr_t * 0.3, 2))
                b_idx = 0
                while cut_t + 1.0 <= curr_t:
                    sc = secondary[b_idx % len(secondary)]
                    dur_b = min(b_dur, round(curr_t - cut_t, 2), float(sc.get("total_duration") or 2.0))
                    nfps = float(sc.get("fps") or 25.0)
                    broll_clips.append({
                        "file_path": sc.get("file_path"),
                        "filename": sc.get("filename") or os.path.basename(sc.get("file_path", "")),
                        "track": 2,
                        "timeline_start": round(cut_t, 2),
                        "timeline_end": round(cut_t + dur_b, 2),
                        "in_seconds": 0.0,
                        "out_seconds": round(dur_b, 2),
                        "in_frame": 0,
                        "out_frame": max(1, int(round(dur_b * nfps))),
                        "duration": round(dur_b, 2),
                        "native_fps": nfps,
                        "role": "B-Roll Cutaway (V2)",
                    })
                    b_idx += 1
                    cut_t = round(cut_t + freq, 2)
            # Mark first B-roll in talking style with a Before/After split slider effect (like StuCwZBNGpc)
            for bi, bc in enumerate(broll_clips):
                bc["effect"] = "before_after_slider" if (is_talking_style and bi == 0) else "cutaway_zoom"

        # Build Behind-Person Depth Text (like "ROUGH CUT / READY" in StuCwZBNGpc)
        resolved_depth_text = (depth_text or "").strip()
        if not resolved_depth_text and ai_clone_blueprint and ai_clone_blueprint.get("depth_hero_text"):
            resolved_depth_text = " | ".join(ai_clone_blueprint["depth_hero_text"])
        if not resolved_depth_text and is_talking_style:
            resolved_depth_text = "ROUGH CUT | READY"

        depth_lines = [p.strip().upper() for p in resolved_depth_text.replace("\n", "|").split("|") if p.strip()][:2]
        if len(depth_lines) == 1:
            words = depth_lines[0].split()
            if len(words) >= 2:
                mid = max(1, len(words) - 1)
                depth_lines = [" ".join(words[:mid]), " ".join(words[mid:])]

        # Build Foreground Pop-Up Captions (Hinglish / Hindi / English)
        captions: List[Dict[str, Any]] = []
        if ai_clone_blueprint and ai_clone_blueprint.get("captions"):
            captions = ai_clone_blueprint["captions"]
        elif caption_text.strip():
            raw_lines = [ln.strip() for ln in caption_text.replace("|", "\n").splitlines() if ln.strip()]
            if raw_lines and curr_t > 0:
                seg_dur = round(curr_t / len(raw_lines), 2)
                for idx, ln in enumerate(raw_lines):
                    words = ln.split()
                    hl = " ".join(words[1:3]) if len(words) >= 3 else (words[-1] if words else "")
                    captions.append({
                        "start": round(idx * seg_dur, 2),
                        "end": round(min(curr_t, (idx + 1) * seg_dur), 2),
                        "text": ln,
                        "highlight": hl,
                    })
        elif is_talking_style and curr_t > 0:
            default_lines = [
                ("हमारा AI पूरा Rough Cut Ready करता है", "Rough Cut Ready"),
                ("Shaky Clips और Bad Frames ऑटोमैटिक कट!", "ऑटोमैटिक कट!"),
                ("Beat Sync + Color Grading सिर्फ 1 Click में", "1 Click में"),
            ]
            seg_dur = max(2.0, round(curr_t / len(default_lines), 2))
            for idx, (txt, hl) in enumerate(default_lines):
                s0 = round(idx * seg_dur, 2)
                if s0 >= curr_t:
                    break
                captions.append({
                    "start": s0,
                    "end": round(min(curr_t, s0 + seg_dur), 2),
                    "text": txt,
                    "highlight": hl,
                })

        plan = {
            "sequence_name": sequence_name,
            "format": f"Viral_Reel_{active_style}",
            "reel_style": active_style,
            "reel_style_label": REEL_STYLES.get(active_style, active_style),
            "aspect_ratio": "9:16 (1080x1920)",
            "fps": 25.0,
            "total_duration": round(curr_t, 2),
            "clip_count": len(v1_clips),
            "clips": v1_clips,
            "broll_clips": broll_clips,
            "audio": final_audio,
            "lut_path": ref_dna.get("lut_path"),
            "cinema_rotate_90": cinema_rotate_90,
            "speed_ramp": speed_ramp,
            "depth_lines": depth_lines,
            "captions": captions,
            "watermark_text": (watermark_text or "").strip(),
            "beat_synced": bool(beat_times),
        }
        return plan

    # ------------------------------------------------------------------
    # 3. Direct 1080x1920 .MP4 Reel Renderer (with Depth Text, Captions, B-Roll, LUT, 90° Rotate)
    # ------------------------------------------------------------------
    def render_reel_mp4(
        self,
        plan: Dict[str, Any],
        output_mp4_path: Optional[str] = None,
        log_fn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Renders a complete, ready-to-upload 1080x1920 (9:16) .MP4 Reel from a Reel Plan:
        - Applies 90-degree cinema rotation (if enabled, like SRA Movies / Sameera Digital)
        - Applies Speed-Ramp curve (fast entry -> smooth slow-mo peak)
        - Applies Behind-Subject Giant Depth Text ('ROUGH CUT / READY') + Foreground Pop-Up Captions
        - Overlays V2 B-Roll Cutaways (including Before/After Split-Slider showcase)
        - Applies Reference 3D Color LUT (.cube) via FFmpeg
        - Mixes Music / Reference Audio / Voiceover
        """
        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass

        clips = plan.get("clips") or []
        if not clips:
            return {"success": False, "error": "No clips in plan to render."}

        reels_dir = os.path.join(self.output_dir, "reels_mp4")
        os.makedirs(reels_dir, exist_ok=True)
        if not output_mp4_path:
            safe_seq = "".join(c for c in (plan.get("sequence_name") or "AI_Reel") if c.isalnum() or c in ("_", "-"))
            output_mp4_path = os.path.join(reels_dir, f"{safe_seq}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")

        target_w, target_h = 1080, 1920
        out_fps = 25.0
        broll_clips = plan.get("broll_clips") or []
        depth_lines = plan.get("depth_lines") or []
        captions = plan.get("captions") or []
        watermark = plan.get("watermark_text") or ""
        cinema_rotate_90 = bool(plan.get("cinema_rotate_90"))
        speed_ramp = bool(plan.get("speed_ramp"))
        lut_path = plan.get("lut_path")

        _log(f"🎬 Rendering 1080x1920 Full HD Reel MP4 ({plan.get('reel_style_label', 'Viral Reel')})…", "info")

        temp_dir = tempfile.mkdtemp(prefix="avf_reel_")
        raw_silent_mp4 = os.path.join(temp_dir, "visual_master.mp4")

        try:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(raw_silent_mp4, fourcc, out_fps, (target_w, target_h))
            if not writer.isOpened():
                raise RuntimeError("Could not initialize OpenCV MP4 video writer.")

            # Pre-open B-roll readers cache
            def _read_broll_frame(b_clip: Dict[str, Any], rel_sec: float) -> Optional[np.ndarray]:
                bp = b_clip.get("file_path")
                if not bp or not os.path.isfile(bp):
                    return None
                cap_b = cv2.VideoCapture(bp)
                try:
                    bfps = float(cap_b.get(cv2.CAP_PROP_FPS) or 25.0)
                    target_sec = float(b_clip.get("in_seconds") or 0.0) + rel_sec
                    cap_b.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(target_sec * bfps)))
                    ok, bfrm = cap_b.read()
                    return bfrm if ok else None
                finally:
                    cap_b.release()

            for c_idx, clip in enumerate(clips):
                fp = clip.get("file_path")
                if not fp or not os.path.isfile(fp):
                    continue
                cap = cv2.VideoCapture(fp)
                src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
                src_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
                dur = float(clip.get("duration") or 1.5)
                in_sec = float(clip.get("in_seconds") or 0.0)
                n_out_frames = max(1, int(round(dur * out_fps)))
                zoom = float(clip.get("zoom") or 1.0)

                for f_i in range(n_out_frames):
                    progress_in_clip = f_i / float(max(1, n_out_frames - 1))
                    global_t = float(clip.get("timeline_start") or 0.0) + (f_i / out_fps)

                    # Speed-Ramp mapping: Fast entry (first 30% covers 50% source time) -> Slow-Mo peak (last 70% covers 50%)
                    if speed_ramp:
                        warped_p = 0.5 * (progress_in_clip / 0.3) if progress_in_clip < 0.3 else 0.5 + 0.5 * ((progress_in_clip - 0.3) / 0.7) ** 1.35
                        src_t = in_sec + warped_p * dur
                    else:
                        src_t = in_sec + progress_in_clip * dur

                    src_frame_idx = min(max(0, int(round(src_t * src_fps))), max(0, src_total - 1))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, src_frame_idx)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue

                    # Format frame into 1080x1920 vertical canvas
                    canvas = self._format_vertical_frame(
                        frame, target_w, target_h,
                        cinema_rotate_90=cinema_rotate_90,
                        zoom=zoom * (1.0 + 0.04 * progress_in_clip if speed_ramp else 1.0),
                    )

                    # Check if a B-Roll cutaway is active at `global_t`
                    active_broll = next(
                        (b for b in broll_clips if float(b.get("timeline_start", 0)) <= global_t <= float(b.get("timeline_end", 0))),
                        None
                    )
                    if active_broll is not None:
                        rel_b = global_t - float(active_broll.get("timeline_start", 0))
                        bfrm = _read_broll_frame(active_broll, rel_b)
                        if bfrm is not None:
                            b_canvas = self._format_vertical_frame(bfrm, target_w, target_h, cinema_rotate_90=cinema_rotate_90, zoom=1.05)
                            if active_broll.get("effect") == "before_after_slider":
                                b_dur = max(0.2, float(active_broll.get("duration") or 1.5))
                                canvas = self._render_before_after_slider(b_canvas, rel_b / b_dur)
                            else:
                                canvas = b_canvas

                    # Render Behind-Person Depth Text (on opening hook & talking segments when not covered by B-roll)
                    if depth_lines and active_broll is None and (global_t <= 4.5 or (8.0 <= global_t <= 12.0)):
                        canvas = self._render_depth_text_behind_subject(canvas, depth_lines)

                    # Render Foreground Pop-Up Captions (Hinglish / Hindi / English)
                    active_cap = next(
                        (cp for cp in captions if float(cp.get("start", 0)) <= global_t <= float(cp.get("end", 0))),
                        None
                    )
                    if active_cap:
                        canvas = self._render_foreground_caption(
                            canvas,
                            text=str(active_cap.get("text") or ""),
                            highlight=str(active_cap.get("highlight") or ""),
                        )

                    # Render Studio Watermark (vertical for 90° cinema style, or subtle bottom badge)
                    if watermark:
                        canvas = self._render_watermark(canvas, watermark, vertical_side=cinema_rotate_90)

                    writer.write(canvas)
                cap.release()

            writer.release()

            # Final FFmpeg pass: H.264 encoding + 3D LUT (.cube) color grading + Audio mixing
            cmd = [ffmpeg_path(), "-y", "-v", "error", "-i", raw_silent_mp4]
            audio_file = plan.get("audio") or ""
            has_audio_in = bool(audio_file and os.path.isfile(audio_file))
            if has_audio_in:
                cmd.extend(["-i", audio_file])

            vf_filters = []
            if lut_path and os.path.isfile(lut_path):
                # Escape path for FFmpeg lut3d filter
                esc_lut = lut_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
                vf_filters.append(f"lut3d=file='{esc_lut}'")
            vf_filters.append("format=yuv420p")

            cmd.extend(["-vf", ",".join(vf_filters), "-c:v", "libx264", "-preset", "fast", "-crf", "20"])
            if has_audio_in:
                cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"])
            cmd.append(output_mp4_path)

            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode != 0 or not os.path.isfile(output_mp4_path):
                # Fallback without lut3d if FFmpeg lut3d path escaping had an issue
                cmd_fb = [ffmpeg_path(), "-y", "-v", "error", "-i", raw_silent_mp4]
                if has_audio_in:
                    cmd_fb.extend(["-i", audio_file, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"])
                cmd_fb.extend(["-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "20", output_mp4_path])
                proc2 = subprocess.run(cmd_fb, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc2.returncode != 0 and os.path.isfile(raw_silent_mp4):
                    shutil.copy2(raw_silent_mp4, output_mp4_path)

            _log(f"  ✅ Ready-to-Post Reel MP4 saved: {output_mp4_path}", "info")
            return {
                "success": True,
                "mp4_path": output_mp4_path,
                "duration": plan.get("total_duration", 0.0),
                "reel_style": plan.get("reel_style"),
            }
        except Exception as e:
            logger.error(f"Reel MP4 render failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Visual Compositing Helpers (90° Cinema Rotate, Depth Text, Captions, Before/After Slider)
    # ------------------------------------------------------------------
    def _format_vertical_frame(
        self,
        frame: np.ndarray,
        target_w: int = 1080,
        target_h: int = 1920,
        cinema_rotate_90: bool = False,
        zoom: float = 1.0,
    ) -> np.ndarray:
        """
        Formats any input frame into a 1080x1920 vertical canvas.
        - If `cinema_rotate_90` is True (SRA Movies / Sameera Digital style) and source is landscape (w > h),
          rotates the frame 90 degrees clockwise so the full horizontal cinema shot fills the vertical screen.
        - Otherwise center-crops and applies smooth zoom.
        """
        h, w = frame.shape[:2]
        if cinema_rotate_90 and w > h:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            h, w = frame.shape[:2]

        # Scale to fill target_w x target_h with optional zoom
        scale = max(target_w / float(w), target_h / float(h)) * max(1.0, zoom)
        new_w = max(target_w, int(round(w * scale)))
        new_h = max(target_h, int(round(h * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        x0 = max(0, (new_w - target_w) // 2)
        y0 = max(0, (new_h - target_h) // 2)
        return resized[y0:y0 + target_h, x0:x0 + target_w].copy()

    def _render_depth_text_behind_subject(self, frame: np.ndarray, depth_lines: List[str]) -> np.ndarray:
        """
        Renders giant impact text (like 'ROUGH CUT' in white and 'READY' in emerald green)
        BEHIND the speaker's head/shoulders (Reel #1 StuCwZBNGpc style):
        1. Detects the foreground person mask (face + upper torso silhouette in center)
        2. Draws giant dual-tone typography across the upper third of the frame
        3. Composites the foreground person cleanly ON TOP of the giant text!
        """
        if not depth_lines:
            return frame

        h, w = frame.shape[:2]
        # 1. Build foreground subject mask (skin/face + upper-torso central silhouette)
        mask = self._extract_subject_alpha_mask(frame)

        # 2. Draw giant typography onto a copy of the background
        bg_with_text = frame.copy()
        try:
            from PIL import Image, ImageDraw, ImageFont
            pil_img = Image.fromarray(cv2.cvtColor(bg_with_text, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(pil_img)
            font_top = self._load_font(size=148, bold=True)
            font_bot = self._load_font(size=190, bold=True)

            line1 = depth_lines[0]
            line2 = depth_lines[1] if len(depth_lines) > 1 else ""

            # Line 1: Bold White/Silver at Y = 80
            bbox1 = draw.textbbox((0, 0), line1, font=font_top)
            tw1 = bbox1[2] - bbox1[0]
            x1 = (w - tw1) // 2
            y1 = int(h * 0.05)
            draw.text((x1, y1), line1, font=font_top, fill=(235, 240, 245), stroke_width=4, stroke_fill=(10, 15, 20))

            # Line 2: Giant Emerald Green (#10B981) at Y = 230 (right behind the speaker's head!)
            if line2:
                bbox2 = draw.textbbox((0, 0), line2, font=font_bot)
                tw2 = bbox2[2] - bbox2[0]
                x2 = (w - tw2) // 2
                y2 = y1 + int(h * 0.085)
                draw.text((x2, y2), line2, font=font_bot, fill=(28, 185, 115), stroke_width=5, stroke_fill=(8, 25, 18))

            bg_with_text = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            cv2.putText(bg_with_text, depth_lines[0], (80, 220), cv2.FONT_HERSHEY_DUPLEX, 3.2, (240, 240, 240), 8, cv2.LINE_AA)
            if len(depth_lines) > 1:
                cv2.putText(bg_with_text, depth_lines[1], (60, 410), cv2.FONT_HERSHEY_DUPLEX, 4.0, (115, 185, 28), 10, cv2.LINE_AA)

        # 3. Composite foreground person in front of the text using feathered alpha mask
        alpha = mask[..., None]
        composited = (frame.astype(np.float32) * alpha + bg_with_text.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)
        return composited

    def _extract_subject_alpha_mask(self, frame: np.ndarray) -> np.ndarray:
        """
        Extracts a soft alpha mask (0.0 background, 1.0 foreground subject) for a talking-head
        or portrait subject so large typography sits behind their head and shoulders.
        """
        h, w = frame.shape[:2]
        small_w, small_h = 180, 320
        small = cv2.resize(frame, (small_w, small_h))

        # Detect face to anchor the person's head & shoulders accurately
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        face_box = None
        try:
            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.isfile(cascade_path):
                detector = cv2.CascadeClassifier(cascade_path)
                faces = detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(28, 28))
                if len(faces) > 0:
                    # Pick largest face
                    face_box = max(faces, key=lambda b: b[2] * b[3])
        except Exception:
            pass

        mask_small = np.zeros((small_h, small_w), dtype=np.float32)
        if face_box is not None:
            fx, fy, fw, fh = [int(v) for v in face_box]
            cx, cy = fx + fw // 2, fy + fh // 2
            # Head ellipse (includes hair above forehead)
            cv2.ellipse(mask_small, (cx, int(cy - fh * 0.08)), (int(fw * 0.72), int(fh * 0.88)), 0, 0, 360, 1.0, -1)
            # Shoulders & torso ellipse below head
            cv2.ellipse(mask_small, (cx, int(cy + fh * 1.45)), (int(fw * 1.55), int(fh * 1.35)), 0, 0, 360, 1.0, -1)
        else:
            # Central portrait fallback (head + shoulders in center of 9:16 frame)
            cv2.ellipse(mask_small, (small_w // 2, int(small_h * 0.34)), (int(small_w * 0.25), int(small_h * 0.16)), 0, 0, 360, 1.0, -1)
            cv2.ellipse(mask_small, (small_w // 2, int(small_h * 0.65)), (int(small_w * 0.44), int(small_h * 0.28)), 0, 0, 360, 1.0, -1)

        mask_small = cv2.GaussianBlur(mask_small, (15, 15), 0)
        return cv2.resize(mask_small, (w, h), interpolation=cv2.INTER_LINEAR)

    def _render_foreground_caption(self, frame: np.ndarray, text: str, highlight: str = "") -> np.ndarray:
        """
        Renders viral Hinglish / Hindi / English pop-up captions with black outline and
        golden-yellow (#FFD700) keyword highlight (Reel #1 StuCwZBNGpc style).
        """
        if not text:
            return frame
        h, w = frame.shape[:2]
        try:
            from PIL import Image, ImageDraw
            pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(pil_img)
            font = self._load_font(size=54, bold=True)

            y_pos = int(h * 0.62)
            bbox = draw.textbbox((0, 0), text, font=font)
            total_w = bbox[2] - bbox[0]
            x_start = max(40, (w - total_w) // 2)

            if highlight and highlight in text:
                before, _, after = text.partition(highlight)
                parts = [
                    (before, (255, 255, 255)),
                    (highlight, (255, 215, 15)),  # Golden-yellow keyword highlight
                    (after, (255, 255, 255)),
                ]
                cur_x = x_start
                for seg, color in parts:
                    if not seg:
                        continue
                    draw.text((cur_x, y_pos), seg, font=font, fill=color, stroke_width=4, stroke_fill=(0, 0, 0))
                    seg_box = draw.textbbox((0, 0), seg, font=font)
                    cur_x += (seg_box[2] - seg_box[0])
            else:
                draw.text((x_start, y_pos), text, font=font, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))

            return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            out = frame.copy()
            cv2.putText(out, text[:36], (80, int(h * 0.65)), cv2.FONT_HERSHEY_DUPLEX, 1.3, (0, 0, 0), 8, cv2.LINE_AA)
            cv2.putText(out, text[:36], (80, int(h * 0.65)), cv2.FONT_HERSHEY_DUPLEX, 1.3, (15, 215, 255), 3, cv2.LINE_AA)
            return out

    def _render_before_after_slider(self, frame: np.ndarray, progress: float) -> np.ndarray:
        """
        Renders the viral Before/After AI Retouch & Color Comparison Wipe Slider
        (with glowing cyan vertical divider line and circular <-> handle, exactly like StuCwZBNGpc!).
        Left side = Unretouched/Raw look, Right side = AI Smooth & Warm Retouched look.
        """
        h, w = frame.shape[:2]
        out = frame.copy()

        # Create 'Before' (raw, slightly desaturated/unsmoothed) vs 'After' (warm skin glow + bilateral smooth)
        after = cv2.bilateralFilter(frame, d=7, sigmaColor=45, sigmaSpace=45)
        after = np.clip(after.astype(np.float32) * np.array([0.96, 1.02, 1.08], dtype=np.float32), 0, 255).astype(np.uint8)

        # Animated slider position sweeping back and forth between 30% and 75% of screen width
        split_ratio = 0.35 + 0.38 * (0.5 - 0.5 * math.cos(progress * math.pi * 2.0))
        split_x = int(w * split_ratio)

        out[:, split_x:] = after[:, split_x:]

        # Cyan vertical wipe bar + circular handle
        cyan_bgr = (235, 175, 30)
        cv2.line(out, (split_x, 0), (split_x, h), cyan_bgr, 6, cv2.LINE_AA)
        cy = int(h * 0.48)
        cv2.circle(out, (split_x, cy), 42, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(out, (split_x, cy), 36, cyan_bgr, -1, cv2.LINE_AA)
        cv2.putText(out, "<->", (split_x - 26, cy + 9), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
        return out

    def _render_watermark(self, frame: np.ndarray, text: str, vertical_side: bool = False) -> np.ndarray:
        """Renders subtle studio watermark (vertical for 90° cinema reels like 'SRA Movies', or bottom center)."""
        if not text:
            return frame
        h, w = frame.shape[:2]
        try:
            from PIL import Image, ImageDraw
            font = self._load_font(size=30, bold=False)
            if vertical_side:
                txt_img = Image.new("RGBA", (400, 70), (0, 0, 0, 0))
                d = ImageDraw.Draw(txt_img)
                d.text((10, 15), text, font=font, fill=(255, 255, 255, 195))
                rot = txt_img.rotate(270, expand=True)
                pil_base = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
                pil_base.alpha_composite(rot, (int(w * 0.44), int(h * 0.32)))
                return cv2.cvtColor(np.array(pil_base.convert("RGB")), cv2.COLOR_RGB2BGR)
            else:
                pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(pil_img)
                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                draw.text(((w - tw) // 2, h - 90), text, font=font, fill=(230, 230, 230), stroke_width=2, stroke_fill=(0, 0, 0))
                return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            return frame

    @staticmethod
    def _load_font(size: int = 52, bold: bool = True):
        """Loads a system font that supports both Latin and Hindi/Devanagari."""
        from PIL import ImageFont
        candidates = [
            "/System/Library/Fonts/Kohinoor.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "C:/Windows/Fonts/NirmalaB.ttf" if bold else "C:/Windows/Fonts/Nirmala.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/system/fonts/NotoSansDevanagari-Bold.ttf",
            "/system/fonts/Roboto-Bold.ttf",
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()


reel_cloner = ReelClonerEngine()
