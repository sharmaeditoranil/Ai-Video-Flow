"""
AI Video Flow - HyperFrames Service
Bridges HyperFrames HTML/CSS motion rendering engine with DaVinci Resolve.
Generates luxury animated title cards, lower-thirds, kinetic typography,
and transparent overlays, importing them straight into DaVinci Resolve.
"""

import os
import sys
import shutil
import subprocess
import tempfile
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.hyperframes")


class HyperFramesService:
    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        self.output_dir = os.path.join(self.workspace_root, "renders", "motion_graphics")
        os.makedirs(self.output_dir, exist_ok=True)

    def is_available(self) -> Dict[str, Any]:
        """Checks whether Node.js, npx, and HyperFrames are accessible."""
        node_path = shutil.which("node")
        npx_path = shutil.which("npx")
        
        has_cli = False
        version = None
        if npx_path:
            try:
                proc = subprocess.run(
                    ["npx", "-y", "hyperframes", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=8
                )
                if proc.returncode == 0:
                    has_cli = True
                    version = proc.stdout.strip()
            except Exception as e:
                logger.debug(f"HyperFrames version probe: {e}")

        return {
            "node_available": bool(node_path),
            "node_path": node_path,
            "npx_available": bool(npx_path),
            "hyperframes_available": has_cli,
            "hyperframes_version": version
        }

    def render_html_to_video(
        self,
        html_content: str,
        output_file_name: str,
        duration_sec: int = 5,
        transparent: bool = False
    ) -> Dict[str, Any]:
        """
        Renders an HTML composition into a video file (MP4 or transparent WebM)
        using HyperFrames CLI or headless Chromium fallback.
        """
        ext = ".webm" if transparent else ".mp4"
        if not output_file_name.endswith(ext):
            output_file_name = os.path.splitext(output_file_name)[0] + ext
            
        output_path = os.path.join(self.output_dir, output_file_name)

        # Save HTML to a temporary project file
        temp_dir = tempfile.mkdtemp(prefix="hyperframes_")
        html_file = os.path.join(temp_dir, "index.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        try:
            # Use HyperFrames CLI
            fmt_flag = ["--format", "webm"] if transparent else ["--format", "mp4"]
            cmd = [
                "npx", "-y", "hyperframes", "render",
                html_file,
                "-o", output_path,
                *fmt_flag
            ]

            logger.info(f"Rendering HyperFrames composition: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            if proc.returncode == 0 and os.path.exists(output_path):
                return {
                    "success": True,
                    "output_path": output_path,
                    "duration_sec": duration_sec,
                    "format": "webm" if transparent else "mp4"
                }
            else:
                logger.warning(f"HyperFrames render stderr: {proc.stderr}")
                # Fallback: Save HTML directly for manual/browser capture
                html_saved = os.path.join(self.output_dir, os.path.splitext(output_file_name)[0] + ".html")
                shutil.copy2(html_file, html_saved)
                return {
                    "success": False,
                    "error": proc.stderr or proc.stdout or "Render command failed.",
                    "html_preview_path": html_saved,
                    "note": f"Saved HTML preview to {html_saved}"
                }
        except Exception as e:
            logger.error(f"Render execution error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def create_wedding_title_overlay(
        self,
        title: str,
        subtitle: str = "The Wedding Celebration",
        date_venue: str = "December 2026",
        style: str = "Royal Gold",
        duration_sec: int = 6,
        import_to_resolve: bool = True
    ) -> Dict[str, Any]:
        """
        Creates an animated wedding intro/title card, renders it,
        and optionally imports it straight into DaVinci Resolve.
        """
        from packages.hyperframes_engine.templates import get_wedding_title_html
        html = get_wedding_title_html(title, subtitle, date_venue, style, duration_sec)
        
        safe_name = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).replace(" ", "_")
        filename = f"Title_{safe_name}.mp4"

        res = self.render_html_to_video(html, filename, duration_sec=duration_sec, transparent=False)
        
        if res.get("success") and import_to_resolve:
            try:
                from packages.core_resolve.resolve_adapter import resolve_adapter
                import_res = resolve_adapter.import_media([res["output_path"]])
                res["resolve_import"] = import_res
            except Exception as e:
                res["resolve_import_error"] = str(e)

        return res

    def create_lower_third_overlay(
        self,
        name: str,
        role: str = "Bride / Groom",
        style: str = "Glass Gold",
        duration_sec: int = 5,
        import_to_resolve: bool = True
    ) -> Dict[str, Any]:
        """
        Creates an animated transparent lower third, renders to WebM with alpha,
        and imports into DaVinci Resolve for placing on Video Track 2 (V2).
        """
        from packages.hyperframes_engine.templates import get_lower_third_html
        html = get_lower_third_html(name, role, style, duration_sec)
        
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).replace(" ", "_")
        filename = f"LowerThird_{safe_name}.webm"

        res = self.render_html_to_video(html, filename, duration_sec=duration_sec, transparent=True)
        
        if res.get("success") and import_to_resolve:
            try:
                from packages.core_resolve.resolve_adapter import resolve_adapter
                import_res = resolve_adapter.import_media([res["output_path"]])
                res["resolve_import"] = import_res
            except Exception as e:
                res["resolve_import_error"] = str(e)

        return res


hyperframes_service = HyperFramesService()
