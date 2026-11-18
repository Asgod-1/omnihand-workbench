"""Loopback dashboard with explicit, session-bound queued position control."""

from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
from urllib.parse import urlsplit
import re

from .device import list_usb_ports


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, monitor, **kwargs):
        self.monitor = monitor
        super().__init__(*args, **kwargs)

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/state":
            return self._json(self.monitor.snapshot())
        if path == "/api/ports":
            return self._json({"ports": list_usb_ports()})
        if path == '/api/control':
            try:return self._json(self.monitor.control.catalog())
            except Exception as exc:return self._json({'error':str(exc)},409)
        if path == "/health":
            state=self.monitor.snapshot()
            return self._json({"ok": True, "app": "omnihand-workbench", "read_only": state['read_only'], "mode":state['mode']})
        files = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/motion-panel.js":"motion-panel.js", "/hand-viewer.js":"hand-viewer.js", "/taxel-surface.js":"taxel-surface.js", "/tactile-layout.js":"tactile-layout.js", "/tactile-view.js":"tactile-view.js", "/view-math.js":"view-math.js", "/favicon.svg": "favicon.svg"}
        asset = path in ('/assets/model/model.json','/assets/three/three.module.js','/assets/three/OrbitControls.js','/assets/three/STLLoader.js','/assets/tactile/o10_tactile_sensor_array.jpg') or re.fullmatch(r'/assets/model/R_[a-z_]+\.STL',path)
        if path in files:
            file = Path(__file__).parent / "web" / files[path]
        elif asset:
            base = (self.monitor.root / 'vendor/web_assets').resolve()
            file = (base / path.removeprefix('/assets/')).resolve()
            if not file.is_relative_to(base):
                return self._json({'error':'Invalid asset path'},404)
        else:
            return self._json({"error": "Not found"}, 404)
        if not file.is_file():
            return self._json({'error':'模型资源尚未安装，请运行 scripts/setup_visualization.py'},404)
        body = file.read_bytes()
        self.send_response(200)
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # No cross-site POSTs into a local device session.
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                allowed = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == self.server.server_port
            except ValueError:
                allowed = False
            if not allowed:
                return self._json({"error": "Origin rejected"}, 403)
        path = urlsplit(self.path).path
        if path not in ("/api/connect", "/api/disconnect", "/api/recording", "/api/shutdown",'/api/control'):
            return self._json({"error": "Not found"}, 404)
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Content-Type must be application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid body length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            if path == "/api/shutdown":
                self._json({"ok": True, "message": "Dashboard service stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == '/api/control':
                result=self.monitor.control.request(body.get('action'),body)
            elif path == "/api/connect":
                mode = body.get("mode", "usb")
                if mode not in ("usb", "canfd", "demo"):
                    raise ValueError("Only USB, Damiao CAN FD or labelled demo mode is available")
                result = self.monitor.connect(body.get("port", "auto"), demo=mode == "demo", transport="canfd" if mode == "canfd" else "usb", hand_type=body.get("hand", "right"), device_id=int(body.get("device_id", 1)), can_channel=int(body.get("can_channel", 1)), can_id=int(body.get("can_id", 9)))
            elif path == "/api/disconnect":
                self.monitor.disconnect()
                result = self.monitor.snapshot()
            else:
                if type(body.get("enabled")) is not bool:
                    raise ValueError("enabled must be a boolean")
                result = self.monitor.set_recording(body["enabled"])
            self._json(result)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 409)

    def log_message(self, format, *args):
        # Avoid flooding the console with periodic snapshot GET requests.
        if args and str(args[1] if len(args) > 1 else "") != "200":
            super().log_message(format, *args)


def make_server(monitor, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, monitor=monitor))
    server.daemon_threads = True
    return server
