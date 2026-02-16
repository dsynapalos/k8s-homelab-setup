"""
Harbor SCANNING_COMPLETED webhook handler.

Receives webhook events from Harbor when Trivy completes a vulnerability scan,
filters for Critical severity, and posts a formatted notification to Matrix.

Runs as a sidecar container alongside the Alertmanager receiver in the
matrix-bridge pod. Listens on port 3001.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler


MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://matrix.monitoring.svc.cluster.local:8008")
MATRIX_TOKEN = os.environ.get("MATRIX_TOKEN", "")
MATRIX_ROOM_ID = os.environ.get("MATRIX_ROOM_ID", "")
LISTEN_PORT = int(os.environ.get("HARBOR_WEBHOOK_PORT", "3001"))


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for Harbor webhook events."""

    def do_POST(self):
        """Handle incoming Harbor webhook."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body)

            event_type = payload.get("type", "")
            if event_type == "SCANNING_COMPLETED":
                self.handle_scan_completed(payload)
            else:
                print(f"Ignoring event type: {event_type}", flush=True)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as e:
            print(f"Error processing webhook: {e}", file=sys.stderr, flush=True)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def handle_scan_completed(self, payload):
        """Process SCANNING_COMPLETED event — only notify on Critical."""
        try:
            event_data = payload.get("event_data", {})
            resources = event_data.get("resources", [])

            for resource in resources:
                scan_overview = resource.get("scan_overview", {})
                report_key = "application/vnd.security.vulnerability.report; version=1.1"
                report = scan_overview.get(report_key, {})

                severity = report.get("severity", "None")
                summary = report.get("summary", {}).get("summary", {})
                fixable = report.get("summary", {}).get("fixable", 0)
                scanner = report.get("scanner", {}).get("name", "unknown")

                critical_count = summary.get("Critical", 0)
                high_count = summary.get("High", 0)
                medium_count = summary.get("Medium", 0)
                low_count = summary.get("Low", 0)

                resource_url = resource.get("resource_url", "unknown")
                tag = resource.get("tag", "unknown")
                repo_name = event_data.get("repository", {}).get("repo_full_name", "unknown")

                # Only notify on Critical severity
                if severity != "Critical":
                    print(f"Scan completed for {repo_name}:{tag} — severity={severity}, skipping (not Critical)", flush=True)
                    continue

                print(f"Critical vulnerability found in {repo_name}:{tag}", flush=True)

                html_body = (
                    f"<h3>🚨 Critical Vulnerability Detected</h3>"
                    f"<p><b>Image</b>: <code>{repo_name}:{tag}</code></p>"
                    f"<p><b>Scanner</b>: {scanner}</p>"
                    f"<table>"
                    f"<tr><th>Severity</th><th>Count</th></tr>"
                    f"<tr><td>🔴 Critical</td><td>{critical_count}</td></tr>"
                    f"<tr><td>🟠 High</td><td>{high_count}</td></tr>"
                    f"<tr><td>🟡 Medium</td><td>{medium_count}</td></tr>"
                    f"<tr><td>🟢 Low</td><td>{low_count}</td></tr>"
                    f"</table>"
                    f"<p><b>Fixable</b>: {fixable}</p>"
                )

                plain_body = (
                    f"🚨 Critical Vulnerability Detected\n"
                    f"Image: {repo_name}:{tag}\n"
                    f"Scanner: {scanner}\n"
                    f"Critical: {critical_count} | High: {high_count} | Medium: {medium_count} | Low: {low_count}\n"
                    f"Fixable: {fixable}"
                )

                post_to_matrix(plain_body, html_body)

        except Exception as e:
            print(f"Error handling scan event: {e}", file=sys.stderr, flush=True)

    def log_message(self, format, *args):
        """Suppress default access logs — only log errors."""
        pass


def post_to_matrix(plain_body, html_body):
    """Send a message to the configured Matrix room."""
    if not MATRIX_TOKEN or not MATRIX_ROOM_ID:
        print("MATRIX_TOKEN or MATRIX_ROOM_ID not set, skipping notification", file=sys.stderr, flush=True)
        return

    txn_id = str(uuid.uuid4())
    encoded_room = urllib.parse.quote(MATRIX_ROOM_ID, safe="")
    url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{encoded_room}/send/m.room.message/{txn_id}"

    message = {
        "msgtype": "m.text",
        "body": plain_body,
        "format": "org.matrix.custom.html",
        "formatted_body": html_body,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(message).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MATRIX_TOKEN}",
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            print(f"Matrix message sent: {resp.status}", flush=True)
    except Exception as e:
        print(f"Failed to send Matrix message: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    print(f"Starting Harbor webhook handler on port {LISTEN_PORT}", flush=True)
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), WebhookHandler)
    server.serve_forever()
