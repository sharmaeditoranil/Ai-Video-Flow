"""
AI Video Flow - Multi-NLE Interchange Exporter
Powered by OpenTimelineIO (Academy Software Foundation).
Exports timelines to .otio, Final Cut Pro XML (.xml), and CMX 3600 (.edl)
for seamless interchange between DaVinci Resolve, Adobe Premiere Pro, and Final Cut Pro.
"""

import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.otio")


class OTIOExporter:
    """
    Constructs standardized editorial timelines and exports to OTIO, XML, and EDL.
    """

    def export_timeline(
        self,
        timeline_name: str,
        clips: List[Dict[str, Any]],
        fps: float = 25.0,
        output_format: str = "xml"
    ) -> Dict[str, Any]:
        """
        Builds timeline structure and exports to requested format: 'xml', 'otio', or 'edl'.
        """
        import opentimelineio as otio

        out_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "renders", "timelines")
        )
        os.makedirs(out_dir, exist_ok=True)

        tl = otio.schema.Timeline(name=timeline_name)
        v_track = otio.schema.Track(name="Video Track 1", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(v_track)

        for clip_info in clips:
            path = clip_info.get("path", "")
            in_sec = clip_info.get("in_point", 0.0)
            out_sec = clip_info.get("out_point", 3.0)
            duration_sec = max(out_sec - in_sec, 0.1)

            rate = fps
            start_frame = int(in_sec * rate)
            duration_frames = int(duration_sec * rate)

            time_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(start_frame, rate),
                duration=otio.opentime.RationalTime(duration_frames, rate)
            )

            media_ref = otio.schema.ExternalReference(target_url=path)
            clip = otio.schema.Clip(
                name=os.path.basename(path) if path else "Clip",
                media_reference=media_ref,
                source_range=time_range
            )
            v_track.append(clip)

        safe_name = "".join(c for c in timeline_name if c.isalnum() or c in ("_", "-"))
        ext = output_format.lower()
        if not ext.startswith("."):
            ext = "." + ext

        output_file = os.path.join(out_dir, f"{safe_name}{ext}")

        adapter_name = "fcp_xml" if ext == ".xml" else ("cmx_3600" if ext == ".edl" else "otio_json")
        try:
            otio.adapters.write_to_file(tl, output_file, adapter_name=adapter_name)
            logger.info(f"Exported timeline to {output_file} (adapter={adapter_name})")
            return {
                "success": True,
                "file_path": output_file,
                "format": output_format,
                "timeline_name": timeline_name,
                "clip_count": len(clips)
            }
        except Exception as e:
            logger.error(f"OTIO export failed for {adapter_name}: {e}")
            # Fallback to direct .otio JSON
            fallback_file = os.path.join(out_dir, f"{safe_name}.otio")
            otio.adapters.write_to_file(tl, fallback_file, adapter_name="otio_json")
            return {
                "success": True,
                "file_path": fallback_file,
                "format": "otio",
                "note": f"Exported as native OTIO due to: {e}"
            }


otio_exporter = OTIOExporter()
