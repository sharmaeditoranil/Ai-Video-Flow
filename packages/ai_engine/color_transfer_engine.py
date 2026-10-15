"""
AI Video Flow - Color Transfer & Reference-Based Grading Engine
Inspired by ColorTransferLib, NeuralPreset, and Reinhard's LAB color transfer.
Analyzes reference mood images or master camera angles, and generates:
1. Perceptual LAB color statistics (mean & standard deviation)
2. 3D Color Look-Up Table (.cube LUT) for DaVinci Resolve
3. Primary Color Wheels CDL (Lift, Gamma, Gain, Saturation) for live Node tuning
"""

import os
import cv2
import numpy as np
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("ai_video_flow.color_transfer")


class ColorTransferEngine:
    """
    Performs reference-based cinematic color grading, multi-cam matching,
    and 3D LUT generation for DaVinci Resolve.
    """

    def __init__(self, luts_dir: Optional[str] = None):
        self.luts_dir = luts_dir or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "renders", "luts")
        )
        os.makedirs(self.luts_dir, exist_ok=True)

    def extract_lab_stats(self, image_np: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Calculates mean and std deviation in LAB color space."""
        lab = cv2.cvtColor(image_np, cv2.COLOR_BGR2LAB).astype(np.float32)
        mean = np.mean(lab, axis=(0, 1))
        std = np.std(lab, axis=(0, 1))
        # Prevent division by zero
        std = np.where(std < 1e-4, 1e-4, std)
        return mean, std

    def match_color_distribution(
        self,
        source_img: np.ndarray,
        reference_img: np.ndarray
    ) -> np.ndarray:
        """
        Applies Reinhard's color transfer to match source colors to reference image mood.
        """
        src_lab = cv2.cvtColor(source_img, cv2.COLOR_BGR2LAB).astype(np.float32)
        s_mean, s_std = self.extract_lab_stats(source_img)
        r_mean, r_std = self.extract_lab_stats(reference_img)

        # Scale and shift channels
        res_lab = (src_lab - s_mean) * (r_std / s_std) + r_mean
        res_lab = np.clip(res_lab, 0, 255).astype(np.uint8)
        return cv2.cvtColor(res_lab, cv2.COLOR_LAB2BGR)

    def generate_3d_lut(
        self,
        reference_image_path: str,
        lut_name: str = "AI_Reference_Look",
        lut_size: int = 33
    ) -> Dict[str, Any]:
        """
        Generates an industry-standard 33x33x33 .cube 3D LUT matching
        the color palette of a reference image.
        The generated LUT can be loaded directly into DaVinci Resolve.
        """
        if not os.path.exists(reference_image_path):
            return {"success": False, "error": f"Reference image not found: {reference_image_path}"}

        ref_img = cv2.imread(reference_image_path)
        if ref_img is None:
            return {"success": False, "error": "Failed to read reference image"}

        # Create neutral 3D RGB lattice
        grid = np.linspace(0.0, 1.0, lut_size, dtype=np.float32)
        r, g, b = np.meshgrid(grid, grid, grid, indexing='ij')
        rgb_lattice = np.stack([b, g, r], axis=-1)  # BGR layout for OpenCV
        flat_lattice = (rgb_lattice.reshape(-1, 1, 3) * 255).astype(np.uint8)

        # Transfer reference color distribution onto lattice
        graded_lattice = self.match_color_distribution(flat_lattice, ref_img)
        graded_rgb = (graded_lattice.reshape(lut_size, lut_size, lut_size, 3) / 255.0).astype(np.float32)

        # Write .cube file
        safe_name = "".join(c for c in lut_name if c.isalnum() or c in ("_", "-"))
        lut_filename = f"{safe_name}.cube"
        lut_path = os.path.join(self.luts_dir, lut_filename)

        with open(lut_path, "w", encoding="utf-8") as f:
            f.write(f"# AI Video Flow Generated 3D LUT\n")
            f.write(f"# Reference Image: {os.path.basename(reference_image_path)}\n")
            f.write(f"TITLE \"{safe_name}\"\n")
            f.write(f"LUT_3D_SIZE {lut_size}\n")
            f.write("DOMAIN_MIN 0.0 0.0 0.0\n")
            f.write("DOMAIN_MAX 1.0 1.0 1.0\n\n")

            # Write values in standard order: red fastest, green next, blue slowest
            for b_idx in range(lut_size):
                for g_idx in range(lut_size):
                    for r_idx in range(lut_size):
                        out_b = graded_rgb[r_idx, g_idx, b_idx, 0]
                        out_g = graded_rgb[r_idx, g_idx, b_idx, 1]
                        out_r = graded_rgb[r_idx, g_idx, b_idx, 2]
                        f.write(f"{out_r:.6f} {out_g:.6f} {out_b:.6f}\n")

        logger.info(f"Generated 3D LUT: {lut_path}")
        return {
            "success": True,
            "lut_path": lut_path,
            "lut_name": safe_name,
            "lut_size": lut_size,
            "reference_image": reference_image_path
        }

    def match_multi_cameras(
        self,
        master_clip_path: str,
        target_clip_path: str,
        lut_name: str = "MultiCam_Match"
    ) -> Dict[str, Any]:
        """
        Analyzes a master camera frame (Cam A) and generates a matching
        3D LUT for target camera (Cam B) to achieve uniform look across angles.
        """
        cap_master = cv2.VideoCapture(master_clip_path)
        cap_target = cv2.VideoCapture(target_clip_path)

        ret_m, frame_m = cap_master.read()
        ret_t, frame_t = cap_target.read()

        cap_master.release()
        cap_target.release()

        if not ret_m or not ret_t:
            return {"success": False, "error": "Could not read frames from both camera clips"}

        # Temp save master frame as reference
        temp_ref_path = os.path.join(self.luts_dir, f"{lut_name}_ref_temp.jpg")
        cv2.imwrite(temp_ref_path, frame_m)

        res = self.generate_3d_lut(temp_ref_path, lut_name=lut_name)
        if os.path.exists(temp_ref_path):
            os.remove(temp_ref_path)

        res["master_clip"] = master_clip_path
        res["target_clip"] = target_clip_path
        return res

    def compute_primary_wheels(
        self,
        reference_image_path: str,
        source_frame_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Translates reference mood into DaVinci Resolve Primary Wheel parameters:
        Lift (shadows tint), Gamma (midtones), Gain (highlights), Saturation.
        """
        if not os.path.exists(reference_image_path):
            return {"success": False, "error": "Reference image does not exist"}

        ref_img = cv2.imread(reference_image_path)
        if ref_img is None:
            return {"success": False, "error": "Could not read reference image"}

        ref_lab = cv2.cvtColor(ref_img, cv2.COLOR_BGR2LAB)
        l_mean = np.mean(ref_lab[:, :, 0]) / 255.0
        a_mean = (np.mean(ref_lab[:, :, 1]) - 128.0) / 128.0
        b_mean = (np.mean(ref_lab[:, :, 2]) - 128.0) / 128.0

        # Estimate primary wheel offsets
        gain_rgb = [
            round(1.0 + (a_mean * 0.15) + (l_mean - 0.5) * 0.1, 3),
            round(1.0 + (l_mean - 0.5) * 0.1, 3),
            round(1.0 - (b_mean * 0.15) + (l_mean - 0.5) * 0.1, 3)
        ]

        gamma_rgb = [
            round(1.0 + (a_mean * 0.08), 3),
            1.0,
            round(1.0 + (b_mean * 0.08), 3)
        ]

        lift_rgb = [
            round(0.0 + (a_mean * 0.04), 3),
            0.0,
            round(0.0 - (b_mean * 0.04), 3)
        ]

        # Saturation estimate
        hsv = cv2.cvtColor(ref_img, cv2.COLOR_BGR2HSV)
        sat = np.mean(hsv[:, :, 1]) / 255.0
        target_sat = round(min(max(sat * 1.5, 0.7), 1.4), 2)

        return {
            "success": True,
            "estimated_wheels": {
                "lift": {"red": float(lift_rgb[0]), "green": float(lift_rgb[1]), "blue": float(lift_rgb[2])},
                "gamma": {"red": float(gamma_rgb[0]), "green": float(gamma_rgb[1]), "blue": float(gamma_rgb[2])},
                "gain": {"red": float(gain_rgb[0]), "green": float(gain_rgb[1]), "blue": float(gain_rgb[2])},
                "saturation": float(target_sat)
            },
            "color_temperature_tint": {
                "temperature": float(round(b_mean * 500, 1)),
                "tint": float(round(a_mean * 200, 1))
            }
        }


color_transfer_engine = ColorTransferEngine()
