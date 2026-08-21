from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEVELOPER_SENTINEL = "developer-sentinel"
USER_SENTINEL = "user-sentinel"
HOST_ENVIRONMENT_VARIABLES = (
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
)


class CaptureServer(ThreadingHTTPServer):
    request_body: dict[str, Any] | None = None
    request_path: str | None = None
    request_received = threading.Event()


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.request_path = self.path
        self.server.request_body = json.loads(body)
        self.server.request_received.set()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"request captured"}}')

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture and verify one local answer-only Codex request."
    )
    parser.add_argument("--codex-command", type=Path, required=True)
    parser.add_argument("--model", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    executable = args.codex_command.resolve(strict=True)
    server = CaptureServer(("127.0.0.1", 0), CaptureHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    environment = os.environ.copy()
    for name in HOST_ENVIRONMENT_VARIABLES:
        environment.pop(name, None)

    port = server.server_address[1]
    provider = (
        '{name="capture",base_url="http://127.0.0.1:'
        f'{port}/v1",wire_api="responses"}}'
    )
    command = [
        str(executable),
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "-c",
        "features.answer_only=true",
        "-c",
        "suppress_unstable_features_warning=true",
        "-c",
        "instructions=" + json.dumps(DEVELOPER_SENTINEL),
        "-c",
        "model_providers.capture=" + provider,
        "-c",
        'model_provider="capture"',
        "--model",
        args.model,
        USER_SENTINEL,
    ]

    try:
        with tempfile.TemporaryDirectory(prefix="codex_request_verify_") as workspace:
            result = subprocess.run(
                command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        if not server.request_received.wait(timeout=5):
            stderr = result.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"Codex did not send a request: {stderr}")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert server.request_body is not None
    if server.request_path != "/v1/responses":
        raise ValueError(f"unexpected request path: {server.request_path}")
    verify_request(server.request_body)
    print("Verified: 0 tools, no host context, one developer and one user input.")


def verify_request(body: dict[str, Any]) -> None:
    if body.get("tool_choice") != "none":
        raise ValueError("tool_choice must be none")
    if body.get("parallel_tool_calls") is not False:
        raise ValueError("parallel_tool_calls must be false")
    if body.get("tools") not in (None, []):
        raise ValueError("tools must be absent or empty")

    input_items = body.get("input")
    if not isinstance(input_items, list):
        raise ValueError("input must be an array")
    if any(item.get("type") == "additional_tools" for item in input_items):
        raise ValueError("AdditionalTools must be absent")

    messages = [item for item in input_items if item.get("type") == "message"]
    if len(messages) != len(input_items):
        raise ValueError("input contains non-message host context")
    roles = [message.get("role") for message in messages]
    if roles not in (["developer", "user"], ["user"]):
        raise ValueError(f"unexpected message roles: {roles}")

    developer_text = body.get("instructions", "")
    if roles == ["developer", "user"]:
        developer_text = message_text(messages[0])
    if developer_text != DEVELOPER_SENTINEL:
        raise ValueError("developer instructions contain unexpected context")
    if message_text(messages[-1]) != USER_SENTINEL:
        raise ValueError("user message contains unexpected context")


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        raise ValueError("message content must be an array")
    text_items = [item.get("text") for item in content if "text" in item]
    if len(text_items) != 1 or not isinstance(text_items[0], str):
        raise ValueError("message must contain exactly one text item")
    return text_items[0]


if __name__ == "__main__":
    main()
