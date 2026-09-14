"""Create the Slack app with a workspace configuration token."""

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key


class ProvisionError(Exception):
    pass


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def slack_call(client, method, token, **payload):
    try:
        response = client.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProvisionError(f"{method}: network or response error") from exc
    if not result.get("ok"):
        code = str(result.get("error", "unknown_error"))
        code = code if re.fullmatch(r"[a-z0-9_]+", code) else "unknown_error"
        pointers = [
            entry["pointer"]
            for entry in result.get("errors", [])
            if isinstance(entry, dict)
            and re.fullmatch(r"/[A-Za-z0-9_/-]+", entry.get("pointer", ""))
        ]
        raise ProvisionError(
            f"{method}: {code}" + (f" ({', '.join(pointers)})" if pointers else "")
        )
    return result


def provision(env_file, manifest_file, action, client):
    values = dotenv_values(env_file)
    token = os.environ.get("SLACK_CONFIG_TOKEN") or values.get("SLACK_CONFIG_TOKEN")
    team = os.environ.get("TACIT_TEAM_ID") or values.get("TACIT_TEAM_ID")
    if not team:
        raise ProvisionError(f"Set TACIT_TEAM_ID in {env_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    state_file = env_file.with_name("slack-app.json")
    if action == "create" and state_file.exists():
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        if saved.get("status") != "created":
            raise ProvisionError(
                "A previous creation has an unknown outcome. Check Slack app management before retrying."
            )
        if saved.get("team_id") != team or saved.get("manifest_sha256") != digest:
            raise ProvisionError("An app already exists for a different workspace or manifest")
        return {"app_id": saved["app_id"], "created": False, "state_file": str(state_file)}
    if not token:
        raise ProvisionError(
            f"Set SLACK_CONFIG_TOKEN in {env_file}; do not pass it on the command line"
        )
    slack_call(client, "apps.manifest.validate", token, manifest=encoded)
    if action == "validate":
        return {"valid": True}

    # Slack creation is not idempotent. Persist intent before the external request.
    private_json(
        state_file, {"status": "creation_pending", "team_id": team, "manifest_sha256": digest}
    )
    result = slack_call(client, "apps.manifest.create", token, manifest=encoded, team_id=team)
    if not re.fullmatch(r"A[A-Z0-9]+", result.get("app_id", "")):
        raise ProvisionError(
            "Slack returned no valid app ID; inspect app management before retrying"
        )
    private_json(
        state_file,
        {
            "status": "created",
            "team_id": team,
            "manifest_sha256": digest,
            "app_id": result["app_id"],
            "credentials": result.get("credentials", {}),
            "oauth_authorize_url": result.get("oauth_authorize_url"),
        },
    )
    set_key(str(env_file), "SLACK_APP_ID", result["app_id"])
    os.chmod(env_file, 0o600)
    return {"app_id": result["app_id"], "created": True, "state_file": str(state_file)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "create"])
    parser.add_argument("--env-file", type=Path, default=Path(".tacit/setup/slack.env"))
    parser.add_argument("--manifest", type=Path, default=Path("slack-manifest.json"))
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=30) as client:
            result = provision(args.env_file, args.manifest, args.action, client)
    except (ProvisionError, OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    if args.action == "validate":
        print("Slack manifest validated.")
    else:
        print(
            f"Slack app: {result['app_id']} ({'created' if result['created'] else 'already recorded'})"
        )
        print(f"Settings: https://api.slack.com/apps/{result['app_id']}")
        print(f"Credentials saved privately: {result['state_file']}")
        print(
            "Next: install the app and configure SLACK_BOT_TOKEN, SLACK_APP_TOKEN and each user token."
        )


if __name__ == "__main__":
    main()
