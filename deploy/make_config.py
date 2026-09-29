"""Generate container configuration for the two-account demo without printing credentials."""

import argparse
import json
import os
import secrets
from pathlib import Path

from tacit.setup import write_env


def main():
    parser = argparse.ArgumentParser(description="Generate deploy/config for the cloud demo")
    parser.add_argument("--team", required=True)
    parser.add_argument("--user-a", required=True)
    parser.add_argument("--user-b", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--slack-url", default="")
    parser.add_argument("--repo-url", default="")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "config")
    args = parser.parse_args()
    if args.user_a == args.user_b:
        parser.error("User A and user B must be different users")
    if "/" in args.domain or ":" in args.domain:
        parser.error("Domain must be a bare host name such as 203-0-113-10.sslip.io")
    args.output.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output, 0o700)
    names = ("relay.env", "slack.env", "worker-a.env", "worker-b.env")
    if any((args.output / name).exists() for name in names):
        parser.error(
            "Configuration already exists; edit existing files or use a different --output"
        )
    bridge, token_a, token_b = (secrets.token_urlsafe(32) for _ in range(3))
    common = {"TACIT_RELAY_URL": f"https://{args.domain}", "TACIT_TEAM_ID": args.team}
    write_env(
        args.output / "relay.env",
        {
            "TACIT_BRIDGE_TOKEN": bridge,
            "TACIT_MEMBERS": json.dumps({args.user_a: token_a, args.user_b: token_b}),
            "TACIT_DB": "/data/relay.sqlite3",
            "TACIT_DEMO_SLACK_URL": args.slack_url,
            "TACIT_REPO_URL": args.repo_url,
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
    for side, owner, token in (("a", args.user_a, token_a), ("b", args.user_b, token_b)):
        write_env(
            args.output / f"worker-{side}.env",
            {
                **common,
                "TACIT_OWNER": owner,
                "TACIT_AGENT_TOKEN": token,
                "TACIT_WORKSPACE": f"/app/workspaces/{side}",
                "TACIT_ALLOWED_ROOTS": f"/app/workspaces/{side}",
                "TACIT_BACKEND": "bedrock",
                "TACIT_MODEL": "",
                "TACIT_BEDROCK_REGION": "us-east-1",
                "TACIT_STATE_DIR": f"/data/worker-{side}",
                "TACIT_CLAIM_WAIT_SECONDS": "30",
                "SLACK_USER_TOKEN": "",
            },
        )
    for name in names:
        print(args.output / name)
    print("Tokens were generated locally. Fill the empty SLACK_* values before starting.")


if __name__ == "__main__":
    main()
