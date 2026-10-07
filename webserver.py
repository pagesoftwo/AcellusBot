"""webserver.py - LAN dashboard for AcellusAuto (stdlib only).

Serves the static site from ../web (index.html/app.js/styles.css) plus a
JSON API the page polls. Lets you watch stats + preview and Start/Pause
the bot from another device on the same network (e.g. your phone).

Endpoints:
  GET  /                  -> dashboard page (static or embedded fallback)
  GET  /api/status        -> {status, activity, uptime_s, stats, rotation,
                              config, build}
  GET  /api/log?n=120     -> {lines: [...]} tail of acellus_auto.log
  GET  /api/shot          -> latest screenshot JPEG (or 204 empty)
  POST /api/start         -> resume automation
  POST /api/pause         -> pause automation

Auth: if dashboard.token is set in config, every /api call needs
?token=... (the page appends it automatically when embedded in the URL
hash or localStorage). Empty token = open LAN (home use).
"""

import io
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_started = {"server": None, "port": None}


def _send_json(handler, obj, code=200):
    try:
        data = json.dumps(obj).encode("utf-8")
    except Exception:
        data = b"{}"
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except Exception:
        pass


FALLBACK_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AcellusAuto</title></head><body style="background:#111;color:#eee;
font-family:sans-serif;padding:24px">
<h2>AcellusAuto is running</h2>
<p>Static site folder <code>web/</code> not found next to the app.</p>
<p>Status API: <a style="color:#7dd3fc" href="/api/status">/api/status</a></p>
</body></html>"""


def make_handler(prov, web_root, token):
    def _static(name):
        # read-through every time: site edits go live without restarting.
        # (Only allow the known files, never arbitrary paths.)
        if name not in ("index.html", "app.js", "styles.css", "favicon.ico"):
            return None
        try:
            with open(web_root + "/" + name, "rb") as f:
                return f.read()
        except Exception:
            return None

    ctype = {".html": "text/html", ".js": "application/javascript",
             ".css": "text/css", ".png": "image/png", ".ico": "image/x-icon"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "AcellusAuto/1"

        def log_message(self, *a):
            pass  # keep console clean; bot log covers errors

        def _auth_ok(self, qs):
            if not token:
                return True
            try:
                got = (qs.get("token") or [""])[0]
            except Exception:
                got = ""
            return bool(got) and got == token

        def _path_qs(self):
            try:
                p = urllib.parse.urlparse(self.path)
                return p.path, urllib.parse.parse_qs(p.query)
            except Exception:
                return "/", {}

        def do_GET(self):
            path, qs = self._path_qs()
            if path == "/" or path == "/index.html":
                page = _static("index.html")
                if page is None:
                    page = FALLBACK_HTML.encode("utf-8")
                    c = "text/html"
                else:
                    c = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", c)
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(page)
                except Exception:
                    pass
                return
            if path in ("/app.js", "/styles.css", "/favicon.ico"):
                data = _static(path.lstrip("/"))
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                ext = "." + path.rsplit(".", 1)[-1]
                self.send_response(200)
                self.send_header("Content-Type", ctype.get(ext, "application/octet-stream"))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except Exception:
                    pass
                return
            if path == "/api/status":
                if not self._auth_ok(qs):
                    _send_json(self, {"error": "forbidden"}, 403)
                    return
                try:
                    _send_json(self, prov["status"]())
                except Exception as e:
                    _send_json(self, {"error": f"{e}"}, 500)
                return
            if path == "/api/log":
                if not self._auth_ok(qs):
                    _send_json(self, {"error": "forbidden"}, 403)
                    return
                try:
                    n = int((qs.get("n") or ["120"])[0])
                except Exception:
                    n = 120
                try:
                    _send_json(self, {"lines": prov["log"](max(1, min(n, 500)))})
                except Exception as e:
                    _send_json(self, {"error": f"{e}"}, 500)
                return
            if path == "/api/shot":
                if not self._auth_ok(qs):
                    _send_json(self, {"error": "forbidden"}, 403)
                    return
                try:
                    data = prov["shot"]()
                except Exception:
                    data = None
                if not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except Exception:
                    pass
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            path, qs = self._path_qs()
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except Exception:
                length = 0
            try:
                if length > 0:
                    self.rfile.read(min(length, 4096))
            except Exception:
                pass
            if path in ("/api/start", "/api/pause"):
                if not self._auth_ok(qs):
                    _send_json(self, {"error": "forbidden"}, 403)
                    return
                try:
                    if path == "/api/start":
                        prov["start"]()
                    else:
                        prov["pause"]()
                    _send_json(self, {"ok": True})
                except Exception as e:
                    _send_json(self, {"error": f"{e}"}, 500)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def start_server(port, token, web_root, prov, host="0.0.0.0"):
    """Start dashboard server in a daemon thread. Returns (server, port).

    host "0.0.0.0" = LAN reachable (phone/other PC). "127.0.0.1" = local
    only (use with a Cloudflare Tunnel for public-domain access).
    """
    handler = make_handler(prov, web_root or "", token or "")
    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port)), handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                         daemon=True)
    t.start()
    _started["server"] = srv
    _started["port"] = srv.server_address[1]
    return srv, srv.server_address[1]


def stop_server():
    try:
        if _started["server"] is not None:
            _started["server"].shutdown()
    except Exception:
        pass
    _started["server"] = None


def lan_ips():
    """Best-effort LAN IPs for the 'open on your phone' hint."""
    out = []
    try:
        import socket as _s
        out.append(_s.gethostbyname(_s.gethostname()))
    except Exception:
        pass
    try:
        import socket as _s2
        s = _s2.socket(_s2.AF_INET, _s2.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    seen = []
    for ip in out:
        if ip and ip not in seen and not ip.startswith("127."):
            seen.append(ip)
    return seen
