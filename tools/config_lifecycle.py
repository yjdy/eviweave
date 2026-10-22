"""Read effective configuration locally with fixed CLI; never starts an inference turn."""

import json
from pathlib import Path
import selectors
import subprocess
import time


def read_config(command, environment, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    messages = [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "eviweave-regression", "version": "0.1.0"}
            },
        },
        {"method": "initialized", "params": {}},
        {
            "id": 2,
            "method": "config/read",
            "params": {"cwd": "/mnt/output", "includeLayers": True},
        },
    ]
    (output / "requests.json").write_text(json.dumps(messages, indent=2) + "\n")
    lines = []
    with (output / "stderr.log").open("w") as stderr:
        proc = subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        buffer = b""

        def request(message, number):
            nonlocal buffer
            proc.stdin.write((json.dumps(message) + "\n").encode())
            proc.stdin.flush()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if b"\n" not in buffer:
                    if not selector.select(0.1):
                        continue
                    import os

                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        raise AssertionError("configuration diagnostic exited early")
                    buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    lines.append(line.decode())
                    item = json.loads(line)
                    if item.get("id") == number:
                        assert "result" in item, item
                        return item["result"]
            raise TimeoutError("configuration diagnostic deadline")

        try:
            initial = request(messages[0], 1)
            proc.stdin.write((json.dumps(messages[1]) + "\n").encode())
            proc.stdin.flush()
            result = request(messages[2], 2)
            assert (
                initial["codexHome"] == "/mnt/home/.codex"
                and "/0.145.0 " in initial["userAgent"]
            )
            (output / "response.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            return result
        finally:
            selector.close()
            proc.stdin.close()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            proc.stdout.close()
            (output / "stdout.log").write_text("\n".join(lines) + "\n")
