import argparse
import logging
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from tacit.client import RelayClient
from tacit.provider import CodexProvider


def required(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Set {name} in your environment file")
    return value


def main():
    parser = argparse.ArgumentParser(description="Tacit: Slack and local Agent Plane")
    parser.add_argument("--env-file", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    relay_parser = sub.add_parser("relay")
    relay_parser.add_argument("--host", default="127.0.0.1")
    relay_parser.add_argument("--port", type=int, default=8765)
    sub.add_parser("slack")
    sub.add_parser("worker")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--probe", action="store_true", help="Run a real Codex file-reading probe")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "relay":
        import uvicorn

        from tacit.relay import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return

    if args.command == "doctor":
        print(f"Codex executable: {shutil.which('codex') or 'MISSING'}")
        for name in (
            "TACIT_RELAY_URL",
            "TACIT_AGENT_TOKEN",
            "TACIT_OWNER",
            "TACIT_TEAM_ID",
            "TACIT_WORKSPACE",
            "SLACK_USER_TOKEN",
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN",
        ):
            print(f"{name}: {'set' if os.environ.get(name) else 'missing'}")
        if shutil.which("codex"):
            subprocess.run(["codex", "login", "status"], check=False, timeout=20)
        if args.probe:
            provider = CodexProvider(
                Path(required("TACIT_WORKSPACE")), os.environ.get("TACIT_MODEL")
            )
            print(
                provider.run(
                    "Use one of the local file excerpts supplied by the runtime. "
                    "Report its relative filename and one factual sentence from it in Korean. "
                    "Do not read parent directories, modify files, contact services, or send messages."
                )
            )
        return

    token_name = "TACIT_AGENT_TOKEN" if args.command == "worker" else "TACIT_BRIDGE_TOKEN"
    relay = RelayClient(required("TACIT_RELAY_URL"), required(token_name))
    try:
        if args.command == "worker":
            from tacit.worker import SlackSender, Worker

            provider = CodexProvider(
                Path(required("TACIT_WORKSPACE")), os.environ.get("TACIT_MODEL")
            )
            owner = required("TACIT_OWNER")
            sender = SlackSender(required("SLACK_USER_TOKEN"), owner, required("TACIT_TEAM_ID"))
            worker = Worker(
                relay, provider, sender, Path(os.environ.get("TACIT_STATE_DIR", f".tacit/{owner}"))
            )
            worker.run()
        else:
            from tacit.slack import run_slack

            run_slack(
                relay,
                required("SLACK_BOT_TOKEN"),
                required("SLACK_APP_TOKEN"),
                required("TACIT_TEAM_ID"),
            )
    except KeyboardInterrupt:
        pass
    finally:
        relay.close()


if __name__ == "__main__":
    main()
