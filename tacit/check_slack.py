"""Check Slack installation and identities without sending messages."""

import argparse
from pathlib import Path

from dotenv import dotenv_values
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError, SlackRequestError


def check(slack_env, worker_env, client_factory=WebClient):
    slack = dotenv_values(slack_env)
    worker = dotenv_values(worker_env)
    findings = []

    def report(name, ok, detail):
        findings.append({"check": name, "ok": ok, "detail": detail})

    team = slack.get("TACIT_TEAM_ID")
    owner = worker.get("TACIT_OWNER")
    report(
        "configuration",
        bool(team and owner and worker.get("TACIT_TEAM_ID") == team),
        "Workspace and owner configured"
        if team and owner and worker.get("TACIT_TEAM_ID") == team
        else "Check TACIT_TEAM_ID and TACIT_OWNER in both environment files",
    )

    for name, values, bot in (
        ("SLACK_BOT_TOKEN", slack, True),
        ("SLACK_USER_TOKEN", worker, False),
    ):
        token = values.get(name)
        if not token:
            report(name, False, "Not configured")
            continue
        try:
            response = client_factory(token=token, timeout=15).auth_test()
            identity_matches = response.get("team_id") == team and (
                bool(response.get("bot_id"))
                if bot
                else response.get("user_id") == owner and not response.get("bot_id")
            )
            scopes = set()
            for key, value in (getattr(response, "headers", None) or {}).items():
                if key.lower() == "x-oauth-scopes":
                    scopes.update(item.strip() for item in value.split(","))
            needed = {"chat:write", "im:write"} | ({"commands"} if bot else set())
            missing = needed - scopes
            if not identity_matches:
                report(name, False, "Token belongs to a different workspace, user, or token type")
            elif missing:
                report(name, False, "Scopes missing or unverified: " + ", ".join(sorted(missing)))
            else:
                report(name, True, "Identity and scopes verified")
        except SlackApiError as exc:
            report(name, False, f"Slack error: {exc.response.get('error', 'unknown_error')}")
        except (SlackRequestError, OSError):
            report(name, False, "Could not reach Slack")

    token = slack.get("SLACK_APP_TOKEN")
    if not token:
        report("SLACK_APP_TOKEN", False, "Not configured")
    else:
        try:
            response = client_factory(token=token, timeout=15).apps_connections_open(
                app_token=token
            )
            # The connection URL contains credentials; never print it.
            report(
                "SLACK_APP_TOKEN",
                bool(response.get("url")),
                "Socket Mode URL requested; no socket opened",
            )
        except SlackApiError as exc:
            report(
                "SLACK_APP_TOKEN",
                False,
                f"Slack error: {exc.response.get('error', 'unknown_error')}",
            )
        except (SlackRequestError, OSError):
            report("SLACK_APP_TOKEN", False, "Could not reach Slack")
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slack-env", type=Path, default=Path(".tacit/setup/slack.env"))
    parser.add_argument("--worker-env", type=Path, default=Path(".tacit/setup/owner.env"))
    args = parser.parse_args()
    for path in (args.slack_env, args.worker_env):
        if not path.is_file():
            parser.error(f"Configuration file not found: {path}")
    findings = check(args.slack_env, args.worker_env)
    for finding in findings:
        print(f"{'OK' if finding['ok'] else 'WAIT'} {finding['check']}: {finding['detail']}")
    raise SystemExit(0 if all(finding["ok"] for finding in findings) else 1)


if __name__ == "__main__":
    main()
