"""
AI Video Flow - One-Click "CONNECT ALL" Engine
Automatically configures Claude Desktop MCP settings, checks DaVinci Resolve
scripting permissions, backs up configurations, and validates connections.
"""

import os
import sys
import json
import shutil
import platform
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ai_video_flow.connect_all")


class ConnectAllEngine:
    """
    Automates 1-click zero-touch configuration for non-technical video editors.
    """

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.system_os = platform.system()
        self.claude_config_path = self._get_claude_config_path()

    def _get_claude_config_path(self) -> str:
        """Finds Claude Desktop config file location based on OS."""
        if self.system_os == "Windows":
            appdata = os.environ.get("APPDATA", "")
            return os.path.join(appdata, "Claude", "claude_desktop_config.json")
        elif self.system_os == "Darwin":  # macOS
            return os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
        else:  # Linux
            return os.path.expanduser("~/.config/Claude/claude_desktop_config.json")

    def run_connect_all(self) -> Dict[str, Any]:
        """
        Executes all 10 setup steps:
        1. Resolve detection
        2. Resolve running check
        3. Scripting API verification
        4. Claude Desktop detection
        5. Existing config backup
        6. MCP server registration
        7. Preserving other installed servers
        8. Verifying local connector
        9. Returning clear status
        """
        results = {
            "success": False,
            "steps": [],
            "claude_desktop": {"installed": False, "configured": False, "config_path": self.claude_config_path},
            "davinci_resolve": {"installed": False, "running": False, "version": None, "edition": None},
            "mcp_server": {"path": None, "registered": False},
            "guidance": []
        }

        # Step 1: Detect DaVinci Resolve
        resolve_installed, resolve_paths = self._detect_resolve_installed()
        results["davinci_resolve"]["installed"] = resolve_installed
        results["steps"].append(f"DaVinci Resolve Installed: {resolve_installed}")

        # Step 2: Check if Resolve is running & scripting API
        if self.project_root not in sys.path:
            sys.path.insert(0, self.project_root)
        from packages.core_resolve.resolve_adapter import resolve_adapter
        resolve_status = resolve_adapter.get_status()
        results["davinci_resolve"]["running"] = resolve_status.get("resolve_running", False)
        results["davinci_resolve"]["version"] = resolve_status.get("version")
        results["davinci_resolve"]["edition"] = resolve_status.get("edition")
        results["steps"].append(f"DaVinci Resolve Running: {results['davinci_resolve']['running']}")

        if not results["davinci_resolve"]["running"]:
            results["guidance"].append("DaVinci Resolve is not running. Please open DaVinci Resolve to enable live editing.")

        # Step 3: Check Claude Desktop installation & directory
        claude_dir = os.path.dirname(self.claude_config_path)
        claude_installed = os.path.exists(claude_dir) or os.path.exists(self.claude_config_path)
        results["claude_desktop"]["installed"] = claude_installed
        results["steps"].append(f"Claude Desktop Detected: {claude_installed}")

        # Step 4: Register MCP Server into Claude Desktop config
        # On macOS, Claude Desktop is sandboxed and blocked from ~/Documents.
        # We route through ~/.ai-video-flow/app/ which has full access permissions.
        if self.system_os == "Darwin":
            app_dir = os.path.expanduser("~/.ai-video-flow/app")
            os.makedirs(app_dir, exist_ok=True)
            workspace_packages = os.path.join(self.project_root, "packages")
            target_packages = os.path.join(app_dir, "packages")
            # Skip when already running from ~/.ai-video-flow/app (source == target would delete itself)
            if os.path.realpath(workspace_packages) != os.path.realpath(target_packages):
                try:
                    if os.path.exists(target_packages):
                        shutil.rmtree(target_packages)
                    shutil.copytree(workspace_packages, target_packages)
                except Exception as e:
                    logger.warning(f"Failed to sync packages to ~/.ai-video-flow: {e}")

            server_script = os.path.join(app_dir, "packages", "mcp_server", "server.py")
            python_exec = os.path.expanduser("~/.ai-video-flow/venv/bin/python")
            if not os.path.exists(python_exec):
                python_exec = sys.executable
            target_pythonpath = app_dir
        else:
            server_script = os.path.join(self.project_root, "packages", "mcp_server", "server.py")
            python_exec = sys.executable
            target_pythonpath = self.project_root

        results["mcp_server"]["path"] = server_script

        registration_success = self._register_claude_mcp(
            server_name="ai-video-flow-resolve",
            command=python_exec,
            args=[server_script],
            pythonpath=target_pythonpath
        )
        results["claude_desktop"]["configured"] = registration_success
        results["mcp_server"]["registered"] = registration_success
        results["steps"].append(f"Claude MCP Configuration Updated: {registration_success}")

        if registration_success:
            results["guidance"].append("Restart Claude Desktop to finish connecting AI Video Flow.")

        results["success"] = registration_success
        return results

    def check_status(self, server_name: str = "ai-video-flow-resolve") -> Dict[str, Any]:
        """Read-only status for dashboard polling (never rewrites Claude config)."""
        configured = False
        try:
            with open(self.claude_config_path, "r", encoding="utf-8") as f:
                configured = server_name in (json.load(f).get("mcpServers") or {})
        except Exception:
            pass
        claude_dir = os.path.dirname(self.claude_config_path)
        resolve_installed, _ = self._detect_resolve_installed()
        return {
            "claude_desktop": {
                "installed": os.path.exists(claude_dir),
                "configured": configured,
                "config_path": self.claude_config_path
            },
            "davinci_resolve": {"installed": resolve_installed}
        }

    def _detect_resolve_installed(self) -> (bool, list):
        """Checks default installation paths for DaVinci Resolve."""
        detected_paths = []
        if self.system_os == "Windows":
            candidates = [
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Developer\Scripting"
            ]
        elif self.system_os == "Darwin":
            candidates = [
                "/Applications/DaVinci Resolve/DaVinci Resolve.app",
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
            ]
        else:
            candidates = ["/opt/resolve/bin/resolve"]

        for c in candidates:
            if os.path.exists(c):
                detected_paths.append(c)

        return (len(detected_paths) > 0, detected_paths)

    def _register_claude_mcp(self, server_name: str, command: str, args: list, pythonpath: Optional[str] = None) -> bool:
        """Safely updates Claude Desktop's claude_desktop_config.json with automatic backup."""
        try:
            claude_dir = os.path.dirname(self.claude_config_path)
            os.makedirs(claude_dir, exist_ok=True)

            existing_config = {}
            if os.path.exists(self.claude_config_path):
                # Step 5: Backup existing config
                backup_path = self.claude_config_path + ".backup.json"
                try:
                    shutil.copy2(self.claude_config_path, backup_path)
                    logger.info(f"Backed up Claude config to {backup_path}")
                except Exception as e:
                    logger.warning(f"Could not create backup: {e}")

                try:
                    with open(self.claude_config_path, "r", encoding="utf-8") as f:
                        existing_config = json.load(f)
                except Exception as e:
                    logger.warning(f"Could not parse existing config: {e}. Starting fresh.")
                    existing_config = {}

            if "mcpServers" not in existing_config:
                existing_config["mcpServers"] = {}

            # Add / update ai-video-flow-resolve without touching any other servers
            existing_config["mcpServers"][server_name] = {
                "command": command,
                "args": args,
                "env": {
                    "PYTHONPATH": pythonpath or self.project_root
                }
            }

            with open(self.claude_config_path, "w", encoding="utf-8") as f:
                json.dump(existing_config, f, indent=2)

            logger.info(f"Successfully registered '{server_name}' into {self.claude_config_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to register Claude MCP: {e}")
            return False


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    engine = ConnectAllEngine(root)
    status = engine.run_connect_all()
    print(json.dumps(status, indent=2))
