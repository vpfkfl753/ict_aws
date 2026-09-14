"""Generate private local configuration without printing or committing credentials."""

import argparse
import json
import os
import secrets
from pathlib import Path


def write_env(path, values):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        for key, value in values.items():
            # dotenv double-quoted values support these escapes without shell evaluation.
            encoded = json.dumps(str(value), ensure_ascii=False)
            stream.write(f"{key}={encoded}\n")


def main():
    parser = argparse.ArgumentParser(description="Generate configuration for two participants")
    parser.add_argument("--team", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--peer", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--relay-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path(".tacit/setup"))
    args = parser.parse_args()
    if args.owner == args.peer:
        parser.error("Owner and peer must be different users")
    workspace = args.workspace.resolve(strict=True)
    if not workspace.is_dir():
        parser.error("Workspace must be a directory")
    args.output.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output, 0o700)
    names = ("relay.env", "slack.env", "owner.env", "peer.env")
    if any((args.output / name).exists() for name in names):
        parser.error(
            "Configuration already exists; edit existing files or use a different --output"
        )
    bridge, owner_token, peer_token = (secrets.token_urlsafe(32) for _ in range(3))
    common = {"TACIT_RELAY_URL": args.relay_url, "TACIT_TEAM_ID": args.team}
    write_env(
        args.output / "relay.env",
        {
            "TACIT_BRIDGE_TOKEN": bridge,
            "TACIT_MEMBERS": json.dumps({args.owner: owner_token, args.peer: peer_token}),
            "TACIT_DB": ".tacit/relay.sqlite3",
        },
    )
    write_env(
        args.output / "slack.env",
        {
            **common,
            "TACIT_BRIDGE_TOKEN": bridge,
            "SLACK_BOT_TOKEN": "",
            "SLACK_APP_TOKEN": "",
        },
    )
    for name, owner, token, root in (
        ("owner.env", args.owner, owner_token, str(workspace)),
        ("peer.env", args.peer, peer_token, "/replace/with/your/context/directory"),
    ):
        write_env(
            args.output / name,
            {
                **common,
                "TACIT_OWNER": owner,
                "TACIT_AGENT_TOKEN": token,
                "TACIT_WORKSPACE": root,
                "TACIT_BACKEND": "codex",
                "TACIT_STATE_DIR": f".tacit/{owner}",
                "SLACK_USER_TOKEN": "",
            },
        )
    for name in names:
        print(args.output / name)
    print("Tokens were generated locally. Give only peer.env to the other participant.")


if __name__ == "__main__":
    main()
