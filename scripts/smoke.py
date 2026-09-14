"""Real HTTP and separate worker processes; Slack is always simulated here."""

import argparse
import multiprocessing
import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from tacit.client import RelayClient
from tacit.provider import CodexProvider
from tacit.relay import create_app
from tacit.store import Store
from tacit.worker import Worker


class SimulatedSlack:
    def send(self, exchange):
        return {"channel": "D_SMOKE", "ts": "1.0"}


class SimulatedModel:
    def run(self, prompt):
        return "SIMULATED: B17 uses p2; B24 uses p3."


def run_worker(url, token, workspace, state_dir, live):
    relay = RelayClient(url, token)
    provider = CodexProvider(Path(workspace)) if live else SimulatedModel()
    try:
        assert Worker(relay, provider, SimulatedSlack(), Path(state_dir)).once()
    finally:
        relay.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    members = {"UAAA": secrets.token_urlsafe(32), "UBBB": secrets.token_urlsafe(32)}
    bridge = secrets.token_urlsafe(32)
    process_context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="tacit-smoke-") as directory:
        store = Store(str(Path(directory) / "relay.db"))
        app = create_app(store, members, bridge)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        relay = RelayClient(url, bridge)
        try:
            for _ in range(50):
                try:
                    relay.request("GET", "/health")
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Smoke relay did not start")
            exchange = relay.request(
                "POST",
                "/v1/exchanges",
                json={
                    "request_key": secrets.token_hex(16),
                    "sender": "UAAA",
                    "recipient": "UBBB",
                    "text": "이번 결과 baseline이랑 비교해봤어?",
                },
            )
            for owner, folder in (("UAAA", "alice"), ("UBBB", "bob")):
                process = process_context.Process(
                    target=run_worker,
                    args=(
                        url,
                        members[owner],
                        str(root / "examples" / folder),
                        str(Path(directory) / owner),
                        args.live_model,
                    ),
                )
                process.start()
                process.join(timeout=280)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
                    raise RuntimeError("Worker timed out")
                if process.exitcode != 0:
                    raise RuntimeError(f"Worker exited with {process.exitcode}")
                print(f"Worker {owner}: process exited successfully", flush=True)
            result = store.latest("UBBB", exchange["id"])
            if result["state"] != "completed":
                raise RuntimeError(f"Exchange ended in {result['state']}: {result['error']}")
            print("Transport: real TCP HTTP on loopback", flush=True)
            print(f"Model: {'real Codex' if args.live_model else 'SIMULATED'}", flush=True)
            print("Slack: SIMULATED, no messages sent", flush=True)
            print("Sender context:\n" + result["context"], flush=True)
            print("Recipient explanation:\n" + result["result"], flush=True)
        finally:
            relay.close()
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    main()
