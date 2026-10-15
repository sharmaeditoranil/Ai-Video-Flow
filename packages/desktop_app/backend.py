"""
AI Video Flow - Local Desktop App Backend Server
Serves static dashboard files and exposes REST API endpoints for:
- Live DaVinci Resolve status
- 1-Click "CONNECT ALL" configuration
- HyperFrames engine status
- Live footage analysis & timeline generation trigger
"""

import os
import sys
import json
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any

# Ensure project root is in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from packages.core_resolve.resolve_adapter import resolve_adapter
from packages.hyperframes_engine.hyperframes_service import hyperframes_service
from installer.connect_all_engine import ConnectAllEngine

logger = logging.getLogger("ai_video_flow.desktop_backend")


class AppRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        static_dir = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=static_dir, **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            status = {
                "resolve": resolve_adapter.get_status(),
                "hyperframes": hyperframes_service.is_available(),
                "claude": ConnectAllEngine(root_dir).run_connect_all()
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/connect":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            engine = ConnectAllEngine(root_dir)
            result = engine.run_connect_all()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return

        elif self.path == "/api/test-resolve":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            status = resolve_adapter.get_status()
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Silence routine static asset HTTP logs
        return


def start_server(port: int = 5678):
    server = HTTPServer(("127.0.0.1", port), AppRequestHandler)
    logger.info(f"AI Video Flow desktop backend running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    start_server()
