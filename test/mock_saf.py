"""Mock /api/hooks/scan — mirrors the real endpoint's contract so the hook
shell logic can be tested without auth or the ML pipeline."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET_MARKERS = ("AKIA", "sk-", "password", "BEGIN RSA")


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            # Decode explicitly as UTF-8 so a mojibake request surfaces as a
            # 400 instead of killing the handler thread.
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.send_response(400); self.end_headers()
            self.wfile.write(json.dumps({"detail": f"bad json: {e}"}).encode())
            return

        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_response(401); self.end_headers()
            self.wfile.write(b'{"detail":"unauthorized"}')
            return

        text = body.get("text", "")
        hits = [m for m in SECRET_MARKERS if m.lower() in text.lower()]
        if hits:
            # Replace the FULL matched token, not just the marker, so redaction
            # swaps the real secret. Find the token(s) containing each marker.
            tokens = [t for t in text.replace("\n", " ").split(" ")
                      if any(m.lower() in t.lower() for m in hits)]
            reps = [{"find": t, "replace": "__REDACTED__"} for t in tokens] or \
                   [{"find": h, "replace": "__REDACTED__"} for h in hits]
            red = text
            for r in reps:
                red = red.replace(r["find"], r["replace"])
            resp = {
                "decision": "deny",
                "detections": len(hits),
                # Quotes/newlines/backslashes on purpose: proves our JSON
                # escaping survives a round trip through the shell.
                "message": 'Blocked: found "%s" \\ in your prompt.\nRemove it.' % hits[0],
                "redacted": red,
                "replacements": reps,
            }
        else:
            resp = {"decision": "allow", "detections": 0, "message": "", "redacted": text}

        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        # Echo what the hook sent, so the test can assert the request shape.
        print("REQ " + json.dumps({k: body.get(k) for k in ("client", "event", "text")}), flush=True)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 8899), H).serve_forever()
