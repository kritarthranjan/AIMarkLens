"""A tiny local HTTP server for testing online features without contacting real services."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockServer:
    """Routes map (method, path) -> handler(request) -> (status, headers, body bytes)."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path = self.path.split("?", 1)[0]
                record = {"method": method, "path": self.path, "headers": dict(self.headers), "body": body}
                server.requests.append(record)
                handler = server.routes.get((method, path))
                truncate = None
                if handler is None:
                    status, headers, payload = 404, {}, b"not found"
                else:
                    status, headers, payload, *rest = handler(record)
                    truncate = rest[0] if rest else None  # simulate a dropped connection
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if truncate is None:
                    self.wfile.write(payload)
                else:
                    self.wfile.write(payload[:truncate])
                    self.wfile.flush()
                    self.close_connection = True

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def json(self, method, path, payload, status=200):
        self.routes[(method, path)] = lambda _r: (status, {"Content-Type": "application/json"},
                                                  json.dumps(payload).encode())

    def raw(self, method, path, payload: bytes, status=200, content_type="application/octet-stream"):
        self.routes[(method, path)] = lambda _r: (status, {"Content-Type": content_type}, payload)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
