"""Tiny mock Anthropic API server for demo recording."""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        # Extract the diff from the user message to make a plausible response
        user_msg = ""
        for msg in body.get("messages", []):
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")

        if "add" in user_msg.lower() or "Add" in user_msg:
            commit_msg = "Add arithmetic helper functions\n\nIntroduce add() and subtract() utilities with docstrings\nfor basic math operations."
        else:
            commit_msg = "Update project files"

        response = {
            "id": "msg_demo",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": commit_msg}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 30},
        }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass  # suppress logs


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18923
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(port, flush=True)
    server.serve_forever()
