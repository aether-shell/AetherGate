"""Isolated CI fixture: deterministic responses and request count, no network calls."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

calls = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"calls": calls}).encode())

    def do_POST(self):
        global calls
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        calls += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": "resp_smoke", "object": "response", "status": "completed", "model": "gpt-5.2",
                                    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "smoke ok"}]}],
                                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}).encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
