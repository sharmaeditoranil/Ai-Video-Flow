"""
AI Video Flow - Comprehensive DaVinci Resolve & AI Editing MCP Server
Exposes exhaustive DaVinci Resolve editing tools (Project, Settings, Bins, Timelines,
Clips, Transforms, Color/LUTs, Titles/Fusion, Fairlight, Render Queue)
plus AI Wedding Footage Culling, Optical Flow Shake Trimming & Beat Synchronization.
"""

import sys
import os
import json
import logging
from typing import Dict, Any, List

# Ensure all logging strictly routes to stderr to protect MCP JSON-RPC stdout channel
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("ai_video_flow.mcp_server")

# Add project root to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from packages.core_resolve.resolve_adapter import resolve_adapter
from packages.ai_engine.in_clip_trimmer import in_clip_trimmer
from packages.ai_engine.beat_analyzer import beat_analyzer
from packages.ai_engine.story_planner import story_planner
from packages.hyperframes_engine.hyperframes_service import hyperframes_service
from packages.ai_engine.color_transfer_engine import color_transfer_engine
from packages.ai_engine.scene_detector import scene_detector_engine
from packages.ai_engine.otio_exporter import otio_exporter
from packages.ai_engine.lecture_editor import lecture_editor_engine


# ----------------------------------------------------------------------
# 25+ EXHAUSTIVE DAVINCI RESOLVE & AI EDITING TOOLS
# ----------------------------------------------------------------------
MCP_TOOLS = [
    # 1. Project Management
    {
        "name": "resolve_get_status",
        "description": "Checks DaVinci Resolve connection status, edition (Studio or Free), version, and active project information.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_create_project",
        "description": "Creates a new project in DaVinci Resolve and sets it as the active project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project to create"}
            },
            "required": ["project_name"]
        }
    },
    {
        "name": "resolve_load_project",
        "description": "Opens an existing DaVinci Resolve project by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Name of the project to open"}
            },
            "required": ["project_name"]
        }
    },
    {
        "name": "resolve_save_project",
        "description": "Saves the current active DaVinci Resolve project.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_get_project_settings",
        "description": "Reads current project settings including timeline frame rate, width, and height.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_set_project_setting",
        "description": "Modifies project settings (e.g. timelineResolutionWidth, timelineResolutionHeight, timelineFrameRate, colorScienceMode).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "setting_name": {"type": "string", "description": "Setting key name"},
                "value": {"type": "string", "description": "Setting value to apply"}
            },
            "required": ["setting_name", "value"]
        }
    },

    # 2. Media Pool & Bins
    {
        "name": "resolve_create_bin",
        "description": "Creates a new folder / bin in the DaVinci Resolve Media Pool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bin_name": {"type": "string", "description": "Name of the bin (e.g. '01_Raw_Footage', '02_Audio')"}
            },
            "required": ["bin_name"]
        }
    },
    {
        "name": "resolve_import_media",
        "description": "Imports video or audio files from local paths into a designated Media Pool bin.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_paths": {"type": "array", "items": {"type": "string"}, "description": "List of file paths"},
                "bin_name": {"type": "string", "description": "Target bin name", "default": "01_Raw_Footage"}
            },
            "required": ["file_paths"]
        }
    },
    {
        "name": "resolve_set_clip_color",
        "description": "Sets a clip's color tag in the Media Pool (Orange, Teal, Blue, Green, Yellow, Pink, Purple).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_name": {"type": "string", "description": "Name of the clip in Media Pool"},
                "color": {"type": "string", "description": "Color name (e.g. 'Teal', 'Orange', 'Green')", "default": "Teal"}
            },
            "required": ["clip_name"]
        }
    },

    # 3. Timeline & Track Operations
    {
        "name": "resolve_create_multicam_clip",
        "description": "Synchronizes multiple camera angles (Cam A, Cam B, Stage Wide, Drone, Wireless Audio) into a single Multi-Camera Clip using Audio Waveform (Sound) or Timecode matching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clip_names": {"type": "array", "items": {"type": "string"}, "description": "Names of camera clips to sync"},
                "multicam_name": {"type": "string", "description": "Name for the multicam clip", "default": "Wedding_MultiCam"},
                "sync_mode": {"type": "string", "enum": ["Sound", "Timecode", "In_Point"], "default": "Sound"}
            },
            "required": ["clip_names"]
        }
    },
    {
        "name": "resolve_create_timeline",
        "description": "Creates a new empty timeline in the current project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeline_name": {"type": "string", "description": "Name for the new timeline"}
            },
            "required": ["timeline_name"]
        }
    },
    {
        "name": "resolve_get_timelines",
        "description": "Lists all timelines available in the current project.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_set_active_timeline",
        "description": "Switches the active timeline in DaVinci Resolve to the specified timeline name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeline_name": {"type": "string", "description": "Name of timeline to activate"}
            },
            "required": ["timeline_name"]
        }
    },
    {
        "name": "resolve_set_clip_transform",
        "description": "Adjusts zoom (scale) and pan/tilt position for a clip on the active timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number (1-indexed)", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track (0-indexed)", "default": 0},
                "zoom_x": {"type": "number", "description": "Horizontal zoom factor (1.0 = normal)", "default": 1.0},
                "zoom_y": {"type": "number", "description": "Vertical zoom factor (1.0 = normal)", "default": 1.0},
                "pan": {"type": "number", "description": "Horizontal position offset", "default": 0.0},
                "tilt": {"type": "number", "description": "Vertical position offset", "default": 0.0}
            },
            "required": []
        }
    },
    {
        "name": "resolve_add_timeline_marker",
        "description": "Adds a colored marker with a custom note at a specific frame on the active timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_id": {"type": "number", "description": "Frame number on timeline"},
                "color": {"type": "string", "description": "Marker color (Cyan, Blue, Green, Yellow, Red)", "default": "Cyan"},
                "name": {"type": "string", "description": "Marker name / headline", "default": "Marker"},
                "note": {"type": "string", "description": "Detailed text note", "default": ""}
            },
            "required": ["frame_id"]
        }
    },
    {
        "name": "resolve_get_timeline_markers",
        "description": "Returns all markers on the active timeline with their frames, colors, and notes.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resolve_delete_timeline_marker",
        "description": "Deletes a marker at the specified frame on the active timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_id": {"type": "number", "description": "Frame number of the marker to delete"}
            },
            "required": ["frame_id"]
        }
    },
    {
        "name": "resolve_set_clip_speed",
        "description": "Adjusts playback speed (Slow Motion 50%, Speed Ramping 200%, or Reverse -100%) and retime process (Optical Flow) for a clip on the timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number (default: 1)", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track (0-indexed)", "default": 0},
                "speed_percent": {"type": "number", "description": "Speed percentage (50.0 for half speed slow-mo, 200.0 for 2x fast, -100.0 for reverse)", "default": 50.0},
                "retime_process": {"type": "string", "enum": ["Optical Flow", "Frame Blend", "Nearest"], "default": "Optical Flow"}
            },
            "required": ["speed_percent"]
        }
    },
    {
        "name": "resolve_set_audio_volume",
        "description": "Adjusts audio volume gain in decibels for a specific audio track in Fairlight.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Audio track number", "default": 1},
                "volume_db": {"type": "number", "description": "Volume level in dB (e.g. -6.0, 0.0, -18.0)", "default": 0.0}
            },
            "required": ["track_index", "volume_db"]
        }
    },
    {
        "name": "resolve_apply_auto_ducking",
        "description": "Applies Auto-Audio Ducking: automatically lowers background music volume whenever dialogue or vows are spoken.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "music_track_index": {"type": "number", "description": "Music audio track number", "default": 2},
                "duck_volume_db": {"type": "number", "description": "Ducked volume level in dB (default: -18.0 dB)", "default": -18.0}
            },
            "required": []
        }
    },
    {
        "name": "resolve_normalize_audio",
        "description": "Normalizes audio track loudness to streaming standards (-14 LUFS for YouTube/Instagram, -23 LUFS for Broadcast).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Audio track number", "default": 1},
                "target_lufs": {"type": "number", "description": "Target loudness in LUFS (default: -14.0)", "default": -14.0}
            },
            "required": []
        }
    },

    # 4. Color & Looks
    {
        "name": "resolve_apply_lut_to_clip",
        "description": "Applies a 3D LUT (.cube file) to a specific clip on the timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0},
                "lut_path": {"type": "string", "description": "Absolute path to .cube LUT file"}
            },
            "required": ["lut_path"]
        }
    },
    {
        "name": "resolve_apply_grade_from_drx",
        "description": "Applies a saved DRX color grade still to clips on the current timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "drx_path": {"type": "string", "description": "Absolute path to .drx color grade file"}
            },
            "required": ["drx_path"]
        }
    },
    {
        "name": "resolve_set_manual_color",
        "description": "Manually adjusts Primary Color Wheels (Lift, Gamma, Gain), Color Temperature (Warm/Cool), Tint, Saturation, and Contrast for a clip on the timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number (default: 1)", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track (0-indexed)", "default": 0},
                "temperature": {"type": "number", "description": "Color temperature Kelvin adjustment (+500 warm, -500 cool)"},
                "tint": {"type": "number", "description": "Tint adjustment (+10 magenta, -10 green)"},
                "contrast": {"type": "number", "description": "Contrast slider (1.0 default, 1.2 high contrast)"},
                "saturation": {"type": "number", "description": "Color saturation (50.0 default, 65.0 vibrant wedding, 0 B&W)"},
                "lift": {"type": "number", "description": "Lift / Shadows offset level"},
                "gamma": {"type": "number", "description": "Gamma / Midtones power level"},
                "gain": {"type": "number", "description": "Gain / Highlights slope level"}
            },
            "required": []
        }
    },
    {
        "name": "resolve_reset_color_grade",
        "description": "Resets all manual color grades, wheels, and nodes on a clip back to raw camera original.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0}
            },
            "required": []
        }
    },
    {
        "name": "resolve_create_wedding_node_tree",
        "description": "Builds a professional 5-node color pipeline on a clip: Node 1 (Exposure/Balance), Node 2 (Face/Skin Smooth), Node 3 (Contrast/Sat), Node 4 (Wedding Look/LUT), Node 5 (Glow/Vignette).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0},
                "style": {"type": "string", "enum": ["Royal Warm", "Teal & Orange", "Moody Cinematic", "Vintage Pastel"], "default": "Royal Warm"}
            },
            "required": []
        }
    },
    {
        "name": "resolve_apply_face_smoothing",
        "description": "Applies Beauty Retouching, Face Smoothing, and Skin Tone enhancement: soft skin texture, warm skin glow, and shine reduction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0},
                "smoothing_amount": {"type": "number", "description": "Skin smoothing intensity (0.0 to 1.0)", "default": 0.5},
                "skin_warmth": {"type": "number", "description": "Skin warmth enhancement (0.0 to 1.0)", "default": 0.3},
                "shine_removal": {"type": "number", "description": "Forehead/cheek shine reduction (0.0 to 1.0)", "default": 0.4}
            },
            "required": []
        }
    },
    {
        "name": "resolve_auto_white_balance",
        "description": "Automatically neutralizes harsh yellow wedding stage lighting or mixed LED casts, calibrating a natural neutral white point.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0},
                "mode": {"type": "string", "description": "Color calibration mode", "default": "Neutral Wedding"}
            },
            "required": []
        }
    },
    {
        "name": "resolve_get_node_graph_info",
        "description": "Inspects all nodes in the Color Page for a clip, showing node count, labels, LUTs, and enabled/bypassed states.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0}
            },
            "required": []
        }
    },
    {
        "name": "resolve_set_node_label",
        "description": "Sets a custom label on a node in the Color Page (e.g. '01_Exposure', '02_Skin_Smooth').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_index": {"type": "number", "description": "1-based node index", "default": 1},
                "label": {"type": "string", "description": "Label text to assign"},
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0}
            },
            "required": ["node_index", "label"]
        }
    },
    {
        "name": "resolve_toggle_node_enabled",
        "description": "Enables or disables (bypasses) a specific node in the Color Page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_index": {"type": "number", "description": "1-based node index", "default": 1},
                "is_enabled": {"type": "boolean", "description": "True to enable, False to bypass/disable", "default": True},
                "track_index": {"type": "number", "description": "Video track number", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0}
            },
            "required": ["node_index", "is_enabled"]
        }
    },

    # 5. Titles & Fusion
    {
        "name": "resolve_create_title",
        "description": "Adds a Text+ title generator (e.g. Wedding Couple Names, Royal Film title) onto a video track.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title_text": {"type": "string", "description": "Text content to display (e.g. 'Rohit weds Anjali')"},
                "track_index": {"type": "number", "description": "Video track number (default: 2)", "default": 2}
            },
            "required": ["title_text"]
        }
    },

    # 6. Deliver & Render Queue
    {
        "name": "resolve_set_render_format",
        "description": "Sets render export container format and video codec (e.g. 'mp4' with 'H264' or 'mov' with 'ProRes').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format_name": {"type": "string", "description": "Container format (mp4, mov)", "default": "mp4"},
                "codec": {"type": "string", "description": "Codec (H264, H265, ProRes)", "default": "H264"}
            },
            "required": ["format_name"]
        }
    },
    {
        "name": "resolve_start_render",
        "description": "Adds the current timeline to the Render Queue and starts rendering.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "Destination directory for rendered video"},
                "preset_name": {"type": "string", "description": "Render preset name", "default": "H.264 Master"}
            },
            "required": ["output_dir"]
        }
    },
    {
        "name": "resolve_get_render_status",
        "description": "Checks the status and progress percentage of a render job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Render job ID returned by resolve_start_render"}
            },
            "required": ["job_id"]
        }
    },

    # 7. AI Wedding Video Automation
    {
        "name": "ai_analyze_wedding_clips",
        "description": "Analyzes raw footage using Optical Flow (camera shake) and Laplacian Variance (blur/focus hunting). Extracts optimal shake-free Golden Windows, discarding bad start/tail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_paths": {"type": "array", "items": {"type": "string"}, "description": "List of video paths"}
            },
            "required": ["video_paths"]
        }
    },
    {
        "name": "ai_analyze_music_beats",
        "description": "Analyzes a wedding music track to extract BPM (tempo), precise beat timestamps, and energy drops for beat-synced editing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Path to background music file (MP3/WAV)"},
                "target_fps": {"type": "number", "description": "Timeline FPS", "default": 25.0}
            },
            "required": ["audio_path"]
        }
    },
    {
        "name": "ai_create_wedding_timelines",
        "description": "Generates 3 automated editing sequences (01_Teaser_60s, 02_Cinematic_Highlight_4m, 03_Full_Wedding_Film) with trimmed clips and beat markers directly inside DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_paths": {"type": "array", "items": {"type": "string"}, "description": "List of raw video clips"},
                "audio_path": {"type": "string", "description": "Optional background music path for beat-sync"},
                "format_type": {"type": "string", "enum": ["all", "teaser", "highlight", "full_film"], "default": "all"}
            },
            "required": ["video_paths"]
        }
    },

    # 9. HyperFrames Motion Graphics & Overlays
    {
        "name": "hyperframes_get_status",
        "description": "Checks HyperFrames motion graphics engine status, Node.js environment, and rendering readiness.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "hyperframes_create_wedding_title",
        "description": "Renders a broadcast-grade animated luxury wedding title card (Royal Gold / Romantic Rose / Modern Minimal) using HyperFrames and automatically imports it into DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Names of couple or film title (e.g. 'Annu & Pavitra')"},
                "subtitle": {"type": "string", "description": "Subtitle text (e.g. 'The Wedding Celebration')", "default": "The Wedding Celebration"},
                "date_venue": {"type": "string", "description": "Date and venue line (e.g. 'December 2026 • Udaipur, Rajasthan')", "default": "December 2026"},
                "style": {"type": "string", "enum": ["Royal Gold", "Romantic Rose", "Modern Minimal"], "default": "Royal Gold"},
                "duration_sec": {"type": "number", "description": "Duration in seconds (e.g. 5 or 6)", "default": 6},
                "import_to_resolve": {"type": "boolean", "description": "Auto import rendered video into DaVinci Resolve Media Pool", "default": True}
            },
            "required": ["title"]
        }
    },
    {
        "name": "hyperframes_create_lower_third",
        "description": "Renders an animated transparent lower third overlay (WebM with alpha) for Bride, Groom, or family, and imports it into DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Person's name or title (e.g. 'Pavitra Sharma')"},
                "role": {"type": "string", "description": "Role or relation (e.g. 'The Bride' or 'Groom\'s Sister')", "default": "The Bride"},
                "style": {"type": "string", "enum": ["Glass Gold"], "default": "Glass Gold"},
                "duration_sec": {"type": "number", "description": "Duration in seconds", "default": 5},
                "import_to_resolve": {"type": "boolean", "description": "Auto import rendered video into DaVinci Resolve Media Pool", "default": True}
            },
            "required": ["name"]
        }
    },
    {
        "name": "hyperframes_render_custom_html",
        "description": "Renders custom HTML/CSS/JS motion composition to MP4 or transparent WebM video using HyperFrames.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "html_content": {"type": "string", "description": "Complete HTML source with animations"},
                "output_filename": {"type": "string", "description": "Filename (e.g. 'custom_intro.mp4')"},
                "duration_sec": {"type": "number", "description": "Duration in seconds", "default": 5},
                "transparent": {"type": "boolean", "description": "Render as transparent overlay (WebM)", "default": False}
            },
            "required": ["html_content", "output_filename"]
        }
    },

    # 10. AI Reference Color Grading & Open-Source Tools (ColorTransfer, PySceneDetect, OTIO)
    {
        "name": "ai_generate_3d_lut_from_image",
        "description": "Analyzes any reference image (e.g. Bollywood look, Pinterest photo, or master grading frame) and generates a 33x33x33 3D .cube LUT file ready for DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_image_path": {"type": "string", "description": "Absolute path to reference image"},
                "lut_name": {"type": "string", "description": "Name of the LUT to generate", "default": "AI_Custom_Look"},
                "lut_size": {"type": "number", "description": "Lattice resolution (e.g. 17 or 33)", "default": 33}
            },
            "required": ["reference_image_path"]
        }
    },
    {
        "name": "ai_match_multi_cameras",
        "description": "Performs Multi-Camera Color Matching between Camera A (master angle) and Camera B (secondary angle), generating a matching 3D LUT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "master_clip_path": {"type": "string", "description": "Path to Master Camera clip (Cam A)"},
                "target_clip_path": {"type": "string", "description": "Path to Secondary Camera clip (Cam B)"},
                "lut_name": {"type": "string", "description": "Name of output matching LUT", "default": "CamA_to_CamB_Match"}
            },
            "required": ["master_clip_path", "target_clip_path"]
        }
    },
    {
        "name": "ai_compute_primary_wheels_from_image",
        "description": "Analyzes reference image mood and translates it into DaVinci Resolve Primary Wheel parameters (Lift, Gamma, Gain, Saturation, Color Temperature, Tint) for interactive tuning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_image_path": {"type": "string", "description": "Path to reference image"}
            },
            "required": ["reference_image_path"]
        }
    },
    {
        "name": "ai_detect_scenes_in_video",
        "description": "Uses PySceneDetect & OpenCV adaptive histogram analysis to automatically split long continuous wedding videos into individual takes and scenes with exact timecodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "Path to video file to scan"},
                "threshold": {"type": "number", "description": "Cut detection threshold (default: 27.0)", "default": 27.0},
                "min_scene_len_sec": {"type": "number", "description": "Minimum scene length in seconds", "default": 1.5}
            },
            "required": ["video_path"]
        }
    },
    {
        "name": "ai_export_timeline_to_nle",
        "description": "Uses OpenTimelineIO (OTIO) to export the assembled wedding cut list to Final Cut Pro XML, Adobe Premiere Pro XML, CMX 3600 EDL, or native .otio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeline_name": {"type": "string", "description": "Name of timeline"},
                "clips": {"type": "array", "items": {"type": "object"}, "description": "List of clip dicts with 'path', 'in_point', 'out_point'"},
                "fps": {"type": "number", "description": "Timeline frame rate", "default": 25.0},
                "output_format": {"type": "string", "enum": ["xml", "otio", "edl"], "default": "xml"}
            },
            "required": ["timeline_name", "clips"]
        }
    },
    {
        "name": "ai_edit_class_lecture",
        "description": "Edits 1-2 hour class recordings, lectures, webinars, or podcasts: automatically detects and cuts silent pauses / dead air, normalizes voice loudness to -14 LUFS, and assembles a clean lecture timeline directly in DaVinci Resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_path": {"type": "string", "description": "Absolute path to the 1.5 - 2 hour class recording"},
                "timeline_name": {"type": "string", "description": "Name for the lecture timeline in Resolve", "default": "Cleaned_Class_Lecture"},
                "silence_threshold_db": {"type": "number", "description": "Silence cutoff decibel threshold (default: 28.0)", "default": 28.0},
                "dynamic_punch_in": {"type": "boolean", "description": "Trending multi-cam punch-in zoom (1.12x) on alternating sentences", "default": True},
                "b_roll_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional list of slides or B-roll clips for Track 2 (V2)"},
                "teacher_name": {"type": "string", "description": "Teacher/Speaker name for animated lower-third"},
                "subject_topic": {"type": "string", "description": "Subject or Topic title"}
            },
            "required": ["video_path"]
        }
    },

    # 11. Hollywood Broadcast & Pro Finishing (Markers, Ken Burns, Audio Fades, Letterbox)
    {
        "name": "resolve_add_timeline_marker",
        "description": "Places a color-coded chapter or synchronization marker on the active DaVinci Resolve timeline (e.g. Blue=Dialogue, Cyan=Chapter, Yellow=Drop).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_id": {"type": "number", "description": "Frame number on timeline", "default": 0},
                "color": {"type": "string", "enum": ["Cyan", "Blue", "Green", "Yellow", "Red", "Pink"], "default": "Cyan"},
                "name": {"type": "string", "description": "Marker label", "default": "Chapter Marker"},
                "note": {"type": "string", "description": "Additional notes"}
            },
            "required": ["frame_id"]
        }
    },
    {
        "name": "resolve_apply_ken_burns",
        "description": "Applies slow cinematic Ken Burns camera motion (Slow Zoom In, Slow Zoom Out, Pan) to B-roll clips or still images.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Video track index", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on track", "default": 0},
                "style": {"type": "string", "enum": ["Slow Zoom In", "Slow Zoom Out", "Pan Left to Right"], "default": "Slow Zoom In"}
            },
            "required": []
        }
    },
    {
        "name": "resolve_set_audio_fade",
        "description": "Applies smooth audio fade in and fade out (in frames) on audio clips to prevent audio pops/clicks at cut points.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "track_index": {"type": "number", "description": "Audio track index", "default": 1},
                "item_index": {"type": "number", "description": "Clip index on audio track", "default": 0},
                "fade_in_frames": {"type": "number", "description": "Fade in length in frames (e.g. 12)", "default": 12},
                "fade_out_frames": {"type": "number", "description": "Fade out length in frames (e.g. 12)", "default": 12}
            },
            "required": []
        }
    },
    {
        "name": "resolve_add_cinematic_letterbox",
        "description": "Applies Hollywood widescreen cinematic blanking bars (2.39:1 or 2.35:1 Anamorphic) to the project output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "aspect_ratio": {"type": "string", "enum": ["2.39:1", "2.35:1"], "default": "2.39:1"}
            },
            "required": []
        }
    },

    # 12. Adobe Premiere Pro Bridge Tools
    {
        "name": "premiere_get_status",
        "description": "Checks Adobe Premiere Pro installation (version, installed paths), running state, and bridge availability.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "premiere_launch",
        "description": "Launches or focuses Adobe Premiere Pro on the editing system.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "premiere_open_timeline",
        "description": "Opens an exported XML timeline sequence directly inside Adobe Premiere Pro.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "xml_path": {"type": "string", "description": "Absolute path to the Premiere-compatible XML timeline file"}
            },
            "required": ["xml_path"]
        }
    },
    {
        "name": "premiere_eval_extendscript",
        "description": "Executes raw ExtendScript code directly inside Adobe Premiere Pro via native OS scripting bridge (zero external servers).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "jsx_code": {"type": "string", "description": "ExtendScript (JavaScript ES3) code to run inside Premiere Pro"}
            },
            "required": ["jsx_code"]
        }
    },
    {
        "name": "premiere_get_project_state",
        "description": "Queries live project state, open project name, path, and all sequences from Adobe Premiere Pro.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "premiere_get_timeline_state",
        "description": "Queries the active sequence timeline in Premiere Pro, returning video tracks, audio tracks, and clip counts.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "premiere_create_sequence",
        "description": "Creates a new sequence inside Adobe Premiere Pro with custom dimensions and frame rate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new sequence"},
                "width": {"type": "integer", "description": "Horizontal resolution in pixels (default 1920, 1080 for vertical)"},
                "height": {"type": "integer", "description": "Vertical resolution in pixels (default 1080, 1920 for vertical)"},
                "fps": {"type": "number", "description": "Frame rate (default 25.0)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "premiere_import_media",
        "description": "Imports an array of media file paths into a target bin inside Adobe Premiere Pro.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_paths": {"type": "array", "items": {"type": "string"}, "description": "List of video/audio file paths to import"},
                "target_bin": {"type": "string", "description": "Name of the target project bin (default 'AI_Media')"}
            },
            "required": ["file_paths"]
        }
    },
    {
        "name": "premiere_create_bin",
        "description": "Creates a new folder bin inside the active Premiere Pro project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bin_name": {"type": "string", "description": "Name of the new bin"}
            },
            "required": ["bin_name"]
        }
    },
    {
        "name": "premiere_add_marker",
        "description": "Places a colored timeline marker with comments at a specified timecode in Premiere Pro.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timecode_seconds": {"type": "number", "description": "Position in seconds where the marker should be placed"},
                "name": {"type": "string", "description": "Marker label or headline"},
                "comments": {"type": "string", "description": "Detailed notes or correction instructions"},
                "color": {"type": "string", "description": "Marker color (Yellow, Green, Cyan, Magenta, Red)"}
            },
            "required": ["timecode_seconds"]
        }
    },
    {
        "name": "premiere_razor_at_time",
        "description": "Splits (razors) all sequence tracks at the given timecode in seconds using the Premiere QE DOM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Exact sequence timecode in seconds to split"}
            },
            "required": ["seconds"]
        }
    },
    {
        "name": "premiere_ripple_delete_range",
        "description": "Razors start and end points and ripple-deletes the range (removing dead-air pauses or false takes).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_seconds": {"type": "number", "description": "Start of range to delete in seconds"},
                "end_seconds": {"type": "number", "description": "End of range to delete in seconds"}
            },
            "required": ["start_seconds", "end_seconds"]
        }
    },
    {
        "name": "premiere_place_broll",
        "description": "Places a B-Roll cutaway clip on Video Track 2 (V2) without breaking speech or A-Roll dialogue on V1/A1.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File path of the B-Roll video clip"},
                "start_seconds": {"type": "number", "description": "Timeline position in seconds where cutaway appears"},
                "duration_seconds": {"type": "number", "description": "Duration of cutaway (default 2.4s)"},
                "track_index": {"type": "integer", "description": "Target video track (default 2 for Track V2)"}
            },
            "required": ["file_path", "start_seconds"]
        }
    },
    {
        "name": "premiere_organize_bins",
        "description": "Automatically organizes project items into professional studio bins (01_A-Roll, 02_B-Roll, 03_Music, 04_Audio, 05_Titles).",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "ai_suggest_broll_cutaways",
        "description": "Analyzes primary cut sequence and available raw clips to calculate intelligent B-Roll cutaway timings for Track V2.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "primary_clips": {"type": "array", "items": {"type": "object"}, "description": "List of primary A-Roll clips"},
                "all_analyzed_clips": {"type": "array", "items": {"type": "object"}, "description": "All available raw footage clips"},
                "target_track": {"type": "integer", "description": "Target video track (default 2)"}
            },
            "required": ["primary_clips", "all_analyzed_clips"]
        }
    }
]


# ----------------------------------------------------------------------
# DISPATCH HANDLER
# ----------------------------------------------------------------------
def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Executes requested tool and returns structured response."""
    logger.info(f"Executing tool '{tool_name}' with args: {arguments}")

    # 1. Project Management
    if tool_name == "resolve_get_status":
        return resolve_adapter.get_status()

    elif tool_name == "resolve_create_project":
        return resolve_adapter.create_project(arguments.get("project_name", "AI_Wedding_Project"))

    elif tool_name == "resolve_load_project":
        return resolve_adapter.load_project(arguments.get("project_name", ""))

    elif tool_name == "resolve_save_project":
        success = resolve_adapter.save_project()
        return {"success": success, "message": "Project saved successfully" if success else "Failed to save or Resolve not running."}

    elif tool_name == "resolve_get_project_settings":
        return resolve_adapter.get_current_project_info()

    elif tool_name == "resolve_set_project_setting":
        setting = arguments.get("setting_name")
        val = arguments.get("value")
        return {"success": resolve_adapter.set_project_setting(setting, val), "setting": setting, "value": val}

    # 2. Media Pool & Bins
    elif tool_name == "resolve_create_bin":
        bin_name = arguments.get("bin_name", "01_Footage")
        return {"success": resolve_adapter.create_bin(bin_name), "bin_name": bin_name}

    elif tool_name == "resolve_import_media":
        file_paths = arguments.get("file_paths", [])
        bin_name = arguments.get("bin_name", "01_Raw_Footage")
        items = resolve_adapter.import_media_files(file_paths, bin_name)
        return {"success": True, "imported_count": len(items), "bin_name": bin_name, "file_paths": file_paths}

    elif tool_name == "resolve_set_clip_color":
        clip = arguments.get("clip_name")
        color = arguments.get("color", "Teal")
        return {"success": resolve_adapter.set_clip_color(clip, color), "clip": clip, "color": color}

    # 3. Timeline & Track Operations
    elif tool_name == "resolve_create_multicam_clip":
        c_names = arguments.get("clip_names", [])
        m_name = arguments.get("multicam_name", "Wedding_MultiCam")
        s_mode = arguments.get("sync_mode", "Sound")
        return resolve_adapter.create_multicam_clip(c_names, m_name, s_mode)

    elif tool_name == "resolve_create_timeline":
        tl_name = arguments.get("timeline_name", "AI_Timeline")
        tl = resolve_adapter.create_timeline(tl_name)
        return {"success": tl is not None, "timeline_name": tl_name}

    elif tool_name == "resolve_get_timelines":
        return {"timelines": resolve_adapter.get_timelines()}

    elif tool_name == "resolve_set_active_timeline":
        tl_name = arguments.get("timeline_name")
        return {"success": resolve_adapter.set_active_timeline(tl_name), "active_timeline": tl_name}

    elif tool_name == "resolve_set_clip_transform":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        zx = float(arguments.get("zoom_x", 1.0))
        zy = float(arguments.get("zoom_y", 1.0))
        pan = float(arguments.get("pan", 0.0))
        tilt = float(arguments.get("tilt", 0.0))
        return {"success": resolve_adapter.set_clip_transform(track, item, zx, zy, pan, tilt)}

    elif tool_name == "resolve_add_timeline_marker":
        frame = int(arguments.get("frame_id", 0))
        color = arguments.get("color", "Cyan")
        name = arguments.get("name", "Marker")
        note = arguments.get("note", "")
        return {"success": resolve_adapter.add_timeline_marker(frame, color, name, note), "frame": frame}

    elif tool_name == "resolve_get_timeline_markers":
        return {"markers": resolve_adapter.get_timeline_markers()}

    elif tool_name == "resolve_delete_timeline_marker":
        frame = int(arguments.get("frame_id", 0))
        return {"success": resolve_adapter.delete_timeline_marker(frame), "deleted_frame": frame}

    elif tool_name == "resolve_set_clip_speed":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        speed = float(arguments.get("speed_percent", 50.0))
        retime = arguments.get("retime_process", "Optical Flow")
        return resolve_adapter.set_clip_speed(track, item, speed, retime)

    elif tool_name == "resolve_set_audio_volume":
        track = int(arguments.get("track_index", 1))
        vol = float(arguments.get("volume_db", 0.0))
        return resolve_adapter.set_audio_volume(track, vol)

    elif tool_name == "resolve_apply_auto_ducking":
        m_track = int(arguments.get("music_track_index", 2))
        duck_vol = float(arguments.get("duck_volume_db", -18.0))
        return resolve_adapter.apply_auto_ducking(m_track, duck_vol)

    elif tool_name == "resolve_normalize_audio":
        track = int(arguments.get("track_index", 1))
        lufs = float(arguments.get("target_lufs", -14.0))
        return resolve_adapter.normalize_audio_track(track, lufs)

    # 4. Color & Looks
    elif tool_name == "resolve_apply_lut_to_clip":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        lut = arguments.get("lut_path", "")
        return {"success": resolve_adapter.apply_lut_to_clip(track, item, lut), "lut_path": lut}

    elif tool_name == "resolve_apply_grade_from_drx":
        drx = arguments.get("drx_path", "")
        return {"success": resolve_adapter.apply_grade_from_drx(drx), "drx_path": drx}

    elif tool_name == "resolve_set_manual_color":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        temp = arguments.get("temperature")
        tint = arguments.get("tint")
        contrast = arguments.get("contrast")
        sat = arguments.get("saturation")
        lift = arguments.get("lift")
        gamma = arguments.get("gamma")
        gain = arguments.get("gain")
        return resolve_adapter.set_manual_color_grading(
            track_index=track,
            item_index=item,
            temperature=float(temp) if temp is not None else None,
            tint=float(tint) if tint is not None else None,
            contrast=float(contrast) if contrast is not None else None,
            saturation=float(sat) if sat is not None else None,
            lift=float(lift) if lift is not None else None,
            gamma=float(gamma) if gamma is not None else None,
            gain=float(gain) if gain is not None else None
        )

    elif tool_name == "resolve_reset_color_grade":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        return resolve_adapter.reset_clip_grade(track, item)

    elif tool_name == "resolve_create_wedding_node_tree":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        style = arguments.get("style", "Royal Warm")
        return resolve_adapter.create_wedding_node_tree(track, item, style)

    elif tool_name == "resolve_apply_face_smoothing":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        smooth = float(arguments.get("smoothing_amount", 0.5))
        warmth = float(arguments.get("skin_warmth", 0.3))
        shine = float(arguments.get("shine_removal", 0.4))
        return resolve_adapter.apply_face_smoothing(track, item, smooth, warmth, shine)

    elif tool_name == "resolve_auto_white_balance":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        mode = arguments.get("mode", "Neutral Wedding")
        return resolve_adapter.apply_auto_white_balance(track, item, mode)

    elif tool_name == "resolve_get_node_graph_info":
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        return resolve_adapter.get_node_graph_info(track, item)

    elif tool_name == "resolve_set_node_label":
        n_idx = int(arguments.get("node_index", 1))
        lbl = arguments.get("label", "")
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        return resolve_adapter.set_node_label(n_idx, lbl, track, item)

    elif tool_name == "resolve_toggle_node_enabled":
        n_idx = int(arguments.get("node_index", 1))
        enabled = bool(arguments.get("is_enabled", True))
        track = int(arguments.get("track_index", 1))
        item = int(arguments.get("item_index", 0))
        return resolve_adapter.set_node_enabled(n_idx, enabled, track, item)

    # 5. Titles & Fusion
    elif tool_name == "resolve_create_title":
        title = arguments.get("title_text", "Wedding Film")
        track = int(arguments.get("track_index", 2))
        return resolve_adapter.create_title(title, track)

    # 6. Deliver & Render Queue
    elif tool_name == "resolve_set_render_format":
        fmt = arguments.get("format_name", "mp4")
        codec = arguments.get("codec", "H264")
        return {"success": resolve_adapter.set_render_format(fmt, codec), "format": fmt, "codec": codec}

    elif tool_name == "resolve_start_render":
        out_dir = arguments.get("output_dir")
        preset = arguments.get("preset_name", "H.264 Master")
        return resolve_adapter.configure_and_start_render(out_dir, preset)

    elif tool_name == "resolve_get_render_status":
        job = arguments.get("job_id")
        return resolve_adapter.get_render_job_status(job)

    # 7. AI Wedding Automation
    elif tool_name == "ai_analyze_wedding_clips":
        paths = arguments.get("video_paths", [])
        results = [in_clip_trimmer.analyze_clip(p) for p in paths if os.path.exists(p)]
        return {"analyzed_count": len(results), "clips": results}

    elif tool_name == "ai_analyze_music_beats":
        audio = arguments.get("audio_path")
        fps = arguments.get("target_fps", 25.0)
        return beat_analyzer.analyze_audio(audio, fps)

    elif tool_name == "ai_create_wedding_timelines":
        v_paths = arguments.get("video_paths", [])
        a_path = arguments.get("audio_path")
        fmt = arguments.get("format_type", "all")

        analyzed = []
        for vp in v_paths:
            try:
                analyzed.append(in_clip_trimmer.analyze_clip(vp))
            except Exception as e:
                logger.warning(f"Clip skip {vp}: {e}")

        plans = {}
        if fmt in ["all", "teaser"]:
            plans["teaser"] = story_planner.plan_teaser(analyzed)
        if fmt in ["all", "highlight"]:
            plans["highlight"] = story_planner.plan_highlight(analyzed)
        if fmt in ["all", "full_film"]:
            plans["full_film"] = story_planner.plan_full_film(analyzed)

        created = []
        if resolve_adapter.is_connected():
            for k, plan in plans.items():
                tname = plan.get("sequence_name", f"AI_{k}")
                if resolve_adapter.create_timeline(tname):
                    created.append(tname)

        return {
            "success": True,
            "resolve_connected": resolve_adapter.is_connected(),
            "timelines_created": created,
            "plans": plans
        }

    # 9. HyperFrames Motion Graphics & Overlays
    elif tool_name == "hyperframes_get_status":
        return hyperframes_service.is_available()

    elif tool_name == "hyperframes_create_wedding_title":
        return hyperframes_service.create_wedding_title_overlay(
            title=arguments.get("title", "Wedding Film"),
            subtitle=arguments.get("subtitle", "The Wedding Celebration"),
            date_venue=arguments.get("date_venue", "December 2026"),
            style=arguments.get("style", "Royal Gold"),
            duration_sec=int(arguments.get("duration_sec", 6)),
            import_to_resolve=arguments.get("import_to_resolve", True)
        )

    elif tool_name == "hyperframes_create_lower_third":
        return hyperframes_service.create_lower_third_overlay(
            name=arguments.get("name", "Name"),
            role=arguments.get("role", "Bride"),
            style=arguments.get("style", "Glass Gold"),
            duration_sec=int(arguments.get("duration_sec", 5)),
            import_to_resolve=arguments.get("import_to_resolve", True)
        )

    elif tool_name == "hyperframes_render_custom_html":
        return hyperframes_service.render_html_to_video(
            html_content=arguments.get("html_content", ""),
            output_file_name=arguments.get("output_filename", "custom.mp4"),
            duration_sec=int(arguments.get("duration_sec", 5)),
            transparent=arguments.get("transparent", False)
        )

    # 10. AI Reference Color Grading & Open-Source Tools
    elif tool_name == "ai_generate_3d_lut_from_image":
        return color_transfer_engine.generate_3d_lut(
            reference_image_path=arguments.get("reference_image_path", ""),
            lut_name=arguments.get("lut_name", "AI_Custom_Look"),
            lut_size=int(arguments.get("lut_size", 33))
        )

    elif tool_name == "ai_match_multi_cameras":
        return color_transfer_engine.match_multi_cameras(
            master_clip_path=arguments.get("master_clip_path", ""),
            target_clip_path=arguments.get("target_clip_path", ""),
            lut_name=arguments.get("lut_name", "CamA_to_CamB_Match")
        )

    elif tool_name == "ai_compute_primary_wheels_from_image":
        return color_transfer_engine.compute_primary_wheels(
            reference_image_path=arguments.get("reference_image_path", "")
        )

    elif tool_name == "ai_detect_scenes_in_video":
        return scene_detector_engine.detect_scenes(
            video_path=arguments.get("video_path", ""),
            threshold=float(arguments.get("threshold", 27.0)),
            min_scene_len_sec=float(arguments.get("min_scene_len_sec", 1.5))
        )

    elif tool_name == "ai_export_timeline_to_nle":
        return otio_exporter.export_timeline(
            timeline_name=arguments.get("timeline_name", "AI_Wedding_Timeline"),
            clips=arguments.get("clips", []),
            fps=float(arguments.get("fps", 25.0)),
            output_format=arguments.get("output_format", "xml")
        )

    elif tool_name == "ai_edit_class_lecture":
        return lecture_editor_engine.assemble_lecture_timeline_in_resolve(
            video_path=arguments.get("video_path", ""),
            timeline_name=arguments.get("timeline_name", "Cleaned_Class_Lecture"),
            silence_threshold_db=float(arguments.get("silence_threshold_db", 28.0)),
            dynamic_punch_in=bool(arguments.get("dynamic_punch_in", True)),
            b_roll_paths=arguments.get("b_roll_paths"),
            teacher_name=arguments.get("teacher_name"),
            subject_topic=arguments.get("subject_topic")
        )

    # 11. Hollywood Broadcast & Pro Finishing
    elif tool_name == "resolve_add_timeline_marker":
        return resolve_adapter.add_timeline_marker(
            frame_id=int(arguments.get("frame_id", 0)),
            color=arguments.get("color", "Cyan"),
            name=arguments.get("name", "Chapter Marker"),
            note=arguments.get("note", "")
        )

    elif tool_name == "resolve_apply_ken_burns":
        return resolve_adapter.apply_ken_burns(
            track_index=int(arguments.get("track_index", 1)),
            item_index=int(arguments.get("item_index", 0)),
            style=arguments.get("style", "Slow Zoom In")
        )

    elif tool_name == "resolve_set_audio_fade":
        return resolve_adapter.set_audio_fade(
            track_index=int(arguments.get("track_index", 1)),
            item_index=int(arguments.get("item_index", 0)),
            fade_in_frames=int(arguments.get("fade_in_frames", 12)),
            fade_out_frames=int(arguments.get("fade_out_frames", 12))
        )

    elif tool_name == "resolve_add_cinematic_letterbox":
        return resolve_adapter.add_cinematic_letterbox(
            aspect_ratio=arguments.get("aspect_ratio", "2.39:1")
        )

    # 12. Adobe Premiere Pro Bridge
    elif tool_name == "premiere_get_status":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.get_status()

    elif tool_name == "premiere_launch":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.launch()

    elif tool_name == "premiere_open_timeline":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.open_timeline(arguments.get("xml_path", ""))

    elif tool_name == "premiere_eval_extendscript":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.eval_extendscript(arguments.get("jsx_code", ""))

    elif tool_name == "premiere_get_project_state":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.get_project_state()

    elif tool_name == "premiere_get_timeline_state":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.get_timeline_state()

    elif tool_name == "premiere_create_sequence":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.create_sequence(
            name=arguments.get("name", "AI_Sequence"),
            width=int(arguments.get("width", 1920)),
            height=int(arguments.get("height", 1080)),
            fps=float(arguments.get("fps", 25.0))
        )

    elif tool_name == "premiere_import_media":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.import_media(
            file_paths=arguments.get("file_paths", []),
            target_bin=arguments.get("target_bin", "AI_Media")
        )

    elif tool_name == "premiere_create_bin":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.create_bin(arguments.get("bin_name", "AI_Bin"))

    elif tool_name == "premiere_add_marker":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.add_marker(
            timecode_seconds=float(arguments.get("timecode_seconds", 0.0)),
            name=arguments.get("name", "AI Marker"),
            comments=arguments.get("comments", ""),
            color=arguments.get("color", "Yellow")
        )

    elif tool_name == "premiere_razor_at_time":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.razor_at_time(float(arguments.get("seconds", 0.0)))

    elif tool_name == "premiere_ripple_delete_range":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.ripple_delete_range(
            start_seconds=float(arguments.get("start_seconds", 0.0)),
            end_seconds=float(arguments.get("end_seconds", 0.0))
        )

    elif tool_name == "premiere_place_broll":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.place_broll_cutaway(
            file_path=arguments.get("file_path", ""),
            start_seconds=float(arguments.get("start_seconds", 0.0)),
            duration_seconds=float(arguments.get("duration_seconds", 2.4)),
            track_index=int(arguments.get("track_index", 2))
        )

    elif tool_name == "premiere_organize_bins":
        from packages.core_premiere.premiere_adapter import premiere_adapter
        return premiere_adapter.organize_bins_automatically()

    elif tool_name == "ai_suggest_broll_cutaways":
        from packages.ai_engine.broll_placer import broll_placer
        return broll_placer.suggest_broll_cutaways(
            primary_clips=arguments.get("primary_clips", []),
            all_analyzed_clips=arguments.get("all_analyzed_clips", []),
            target_track=int(arguments.get("target_track", 2))
        )

    else:
        raise ValueError(f"Unknown tool: '{tool_name}'")


# ----------------------------------------------------------------------
# JSON-RPC 2.0 PROTOCOL ENGINE
# ----------------------------------------------------------------------
def send_response(response_dict: Dict[str, Any]) -> None:
    data = json.dumps(response_dict)
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def run_mcp_server() -> None:
    logger.info("AI Video Flow Comprehensive Resolve Server started. Listening on stdio...")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            send_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        logger.info(f"Handling: method={method}, id={msg_id}")

        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ai-video-flow-resolve", "version": "1.0.0"}
                }
            })

        elif method == "notifications/initialized":
            logger.info("Client acknowledged initialization.")

        elif method == "ping":
            send_response({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif method == "tools/list":
            send_response({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}})

        elif method == "tools/call":
            tname = params.get("name")
            args = params.get("arguments", {})
            try:
                res = handle_tool_call(tname, args)
                content = json.dumps(res, indent=2, ensure_ascii=False)
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": content}]}
                })
            except Exception as e:
                logger.error(f"Tool error: {e}", exc_info=True)
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"Error executing '{tname}': {str(e)}"}], "isError": True}
                })

        else:
            if msg_id is not None:
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method '{method}' not found"}
                })


if __name__ == "__main__":
    run_mcp_server()
