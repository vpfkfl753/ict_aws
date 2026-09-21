"""Manage the central relay, Slack connector and ngrok with user systemd units."""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

MARKER = "# Managed by tacit.server\n"
TARGET = "tacit-central.target"
SERVICES = ("tacit-relay.service", "tacit-slack.service", "tacit-ngrok.service")
LOCAL_URL = "http://127.0.0.1:8765"


def public_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Use an HTTPS origin without a path, credentials or port")
    return value.rstrip("/")


def quote(value, *, command=False):
    value = str(value)
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Paths and arguments must not contain control characters")
    value = value.replace("%", "%%")
    if command:
        return json.dumps(value, ensure_ascii=False)
    return value


def render_units(root, ngrok, url):
    root = root.resolve()
    url = public_url(url)
    python = root / ".venv/bin/python"

    def service(description, args, after=""):
        return (
            MARKER
            + f"[Unit]\nDescription={description}\nPartOf={TARGET}\n"
            + f"After=network-online.target {after}\n"
            + "StartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
            + f"WorkingDirectory={quote(root)}\n"
            + "ExecStart=:"
            + " ".join(quote(arg, command=True) for arg in args)
            + "\n"
            + "Restart=on-failure\nRestartSec=10\nTimeoutStopSec=30\n"
            + "KillMode=control-group\nUMask=0077\nNoNewPrivileges=yes\n"
            + "Environment=PYTHONUNBUFFERED=1\n"
            + "StandardOutput=journal\nStandardError=journal\n"
        )

    def tacit(name, env):
        return [python, "-m", "tacit.cli", "--env-file", root / ".tacit/setup" / env, name]

    return {
        TARGET: (
            MARKER
            + "[Unit]\nDescription=Tacit central server\n"
            + "Wants="
            + " ".join(SERVICES)
            + "\n\n[Install]\nWantedBy=default.target\n"
        ),
        SERVICES[0]: service("Tacit relay (loopback only)", tacit("relay", "relay.env")),
        SERVICES[1]: service("Tacit Slack connector", tacit("slack", "slack.env"), SERVICES[0]),
        SERVICES[2]: service(
            "Tacit fixed ngrok endpoint",
            [ngrok, "http", LOCAL_URL, "--url", url, "--inspect=false", "--log=stdout"],
            SERVICES[0],
        ),
    }


def systemctl(*args, check=True, capture=False):
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        text=True,
        capture_output=capture,
        timeout=60,
    )


def install(root, url, ngrok, unit_dir=None, bin_dir=None):
    root = root.resolve()
    url = public_url(url)
    for relative in (
        ".venv/bin/python",
        ".venv/bin/tacit-server",
        ".tacit/setup/relay.env",
        ".tacit/setup/slack.env",
    ):
        if not (root / relative).is_file():
            raise ValueError(f"Missing {relative}; run uv sync and configure Slack first")
    if not ngrok or not Path(ngrok).is_file():
        raise ValueError("Install ngrok first and make it available on PATH")
    unit_dir = unit_dir or Path.home() / ".config/systemd/user"
    bin_dir = bin_dir or Path.home() / ".local/bin"
    units = render_units(root, Path(ngrok).resolve(), url)
    launcher = bin_dir / "tacit-server"
    executable = root / ".venv/bin/tacit-server"
    # Check all ownership boundaries before changing any existing files.
    for name in units:
        path = unit_dir / name
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(MARKER)):
            raise ValueError(f"Refusing to replace unmanaged unit: {path}")
    if launcher.is_symlink():
        if launcher.resolve() != executable.resolve():
            raise ValueError(f"Refusing to replace another launcher: {launcher}")
    elif launcher.exists():
        raise ValueError(f"Refusing to replace existing file: {launcher}")
    unit_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        (unit_dir / name).write_text(content, encoding="utf-8")
    if not launcher.is_symlink():
        launcher.symlink_to(executable)
    config = root / ".tacit/setup/server.json"
    config.write_text(
        json.dumps({"url": url, "ngrok": str(Path(ngrok).resolve())}, indent=2) + "\n"
    )
    config.chmod(0o600)
    systemctl("daemon-reload")
    print(f"Installed {TARGET}. URL: {url}")
    print("Central only: relay + Slack + ngrok. Workers are managed separately.")
    print("Run loginctl enable-linger $USER once to allow startup before login.")
    print("Then use tacit-server on / off / status / logs from any directory.")


def load_config(root):
    path = root / ".tacit/setup/server.json"
    if not path.is_file():
        raise ValueError("Run tacit-server install --url https://YOUR_ASSIGNED_DOMAIN first")
    config = json.loads(path.read_text())
    config["url"] = public_url(config["url"])
    return config


def status():
    for name in (TARGET, *SERVICES):
        result = systemctl(
            "show",
            name,
            "--property=ActiveState,SubState,MainPID,UnitFileState,NRestarts",
            check=False,
            capture=True,
        )
        print(f"{name}: " + ", ".join(result.stdout.strip().splitlines()))


def wait_ready(root, url, timeout=45):
    token = dotenv_values(root / ".tacit/setup/slack.env").get("TACIT_BRIDGE_TOKEN")
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=5, headers={"ngrok-skip-browser-warning": "1"}) as client:
        while time.monotonic() < deadline:
            try:
                local = client.get(LOCAL_URL + "/health")
                public = client.get(url + "/health")
                protected = client.get(url + "/v1/agents")
                authorized = client.get(
                    url + "/v1/agents", headers={"Authorization": f"Bearer {token}"}
                )
                active = systemctl("is-active", *SERVICES, check=False, capture=True)
                if (
                    local.status_code == public.status_code == authorized.status_code == 200
                    and local.json() == public.json()
                    and local.json().get("status") == "ok"
                    and local.json().get("protocol") in {1, 2}
                    and protected.status_code == 401
                    and len(authorized.json()) >= 2
                    and active.stdout.splitlines() == ["active"] * len(SERVICES)
                ):
                    print(f"Central ready: {url} (health + relay authentication verified)")
                    print("Slack Socket Mode readiness: check tacit-server logs or /tacit-status.")
                    return
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            time.sleep(2)
    raise ValueError(
        "Readiness not confirmed. Services remain enabled; inspect tacit-server logs, or run off."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(sys.prefix).parent)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser(
        "install", help="Install persistent user services, without starting them"
    )
    setup.add_argument("--url", required=True)
    for command in ("on", "off", "restart", "status"):
        sub.add_parser(command)
    logs = sub.add_parser("logs")
    logs.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    try:
        if args.command == "install":
            install(root, args.url, shutil.which("ngrok"))
        elif args.command == "off":
            systemctl("disable", "--now", TARGET)
            # Also stop orphaned members if the target was already inactive.
            systemctl("stop", *SERVICES)
            print("Central stopped; automatic startup disabled. Worker processes are unchanged.")
        elif args.command == "status":
            status()
        elif args.command == "logs":
            command = ["journalctl", "--user", "--no-pager", "-n", "80"]
            for name in SERVICES:
                command.extend(["-u", name])
            if args.follow:
                command.append("--follow")
            subprocess.run(command, check=False)
        else:
            config = load_config(root)
            check = subprocess.run(
                [config["ngrok"], "config", "check"], capture_output=True, timeout=20
            )
            if check.returncode:
                raise ValueError("Configure ngrok authtoken first with ngrok config add-authtoken")
            systemctl("enable", TARGET)
            if args.command == "restart":
                systemctl("restart", TARGET)
            else:
                systemctl("start", TARGET, *SERVICES)
            wait_ready(root, config["url"])
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
