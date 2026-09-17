import json
import shutil
import subprocess
import sys

import pytest

from tacit import server
from tacit.client import RelayClient

URL = "https://relay.example.com"


def project(tmp_path):
    root = tmp_path / "project with spaces"
    for name in (
        ".venv/bin/python",
        ".venv/bin/tacit-server",
        ".tacit/setup/relay.env",
        ".tacit/setup/slack.env",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-settings")
    ngrok = tmp_path / "ngrok"
    ngrok.touch()
    return root, ngrok


def test_units_group_only_central_services_and_restart(tmp_path):
    root, ngrok = project(tmp_path)
    units = server.render_units(root, ngrok, URL)
    assert set(units) == {server.TARGET, *server.SERVICES}
    assert "WantedBy=default.target" in units[server.TARGET]
    for name in server.SERVICES:
        assert f"PartOf={server.TARGET}" in units[name]
        assert "Restart=on-failure" in units[name]
        assert "KillMode=control-group" in units[name]
        assert "ExecStart=:" in units[name]
        assert f"WorkingDirectory={root}" in units[name]
        assert "private-settings" not in units[name]
        assert "worker" not in units[name]
    assert "http://127.0.0.1:8765" in units["tacit-ngrok.service"]
    assert '"--url" "' + URL + '"' in units["tacit-ngrok.service"]
    assert "--inspect=false" in units["tacit-ngrok.service"]


def test_systemd_escaping_does_not_expand_path_specifiers_or_environment():
    assert server.quote("/home/%u/$HOME", command=True) == '"/home/%%u/$HOME"'
    assert server.quote("/home/%u/$HOME") == "/home/%%u/$HOME"
    with pytest.raises(ValueError):
        server.quote("/tmp/hello\nExecStart=/bin/false")


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd not installed")
def test_rendered_units_pass_systemd_parser_with_spaces_and_specifiers(tmp_path):
    root = tmp_path / "project with spaces %u $HOME"
    binary = root / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(sys.executable)
    units = server.render_units(root, shutil.which("true"), URL)
    paths = []
    for name, content in units.items():
        path = tmp_path / name
        path.write_text(content)
        paths.append(str(path))
    result = subprocess.run(
        ["systemd-analyze", "--user", "verify", *paths], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com:443",
        "https://example.com\n",
    ],
)
def test_public_url_rejects_unsafe_or_non_origin_values(url):
    with pytest.raises(ValueError):
        server.public_url(url)


def test_install_is_repeatable_and_preserves_credentials(tmp_path, monkeypatch):
    root, ngrok = project(tmp_path)
    calls = []
    monkeypatch.setattr(server, "systemctl", lambda *args, **kwargs: calls.append(args))
    units, bins = tmp_path / "units", tmp_path / "bin"
    for _ in range(2):
        server.install(root, URL, ngrok, units, bins)
    assert calls == [("daemon-reload",)] * 2
    assert (bins / "tacit-server").resolve() == root / ".venv/bin/tacit-server"
    assert (root / ".tacit/setup/relay.env").read_text() == "private-settings"
    config = root / ".tacit/setup/server.json"
    assert json.loads(config.read_text())["url"] == URL
    assert config.stat().st_mode & 0o777 == 0o600


def test_install_refuses_unmanaged_units_before_writing(tmp_path, monkeypatch):
    root, ngrok = project(tmp_path)
    units = tmp_path / "units"
    units.mkdir()
    occupied = units / "tacit-ngrok.service"
    occupied.write_text("someone else's service")
    with pytest.raises(ValueError, match="unmanaged"):
        server.install(root, URL, ngrok, units, tmp_path / "bin")
    assert occupied.read_text() == "someone else's service"
    assert not (units / server.TARGET).exists()


def test_off_disables_boot_and_stops_orphaned_members(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["tacit-server", "off"])
    monkeypatch.setattr(server, "systemctl", lambda *args, **kwargs: calls.append(args))
    server.main()
    assert calls == [("disable", "--now", server.TARGET), ("stop", *server.SERVICES)]


def test_on_validates_ngrok_then_enables_and_starts_members(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["tacit-server", "--project", str(tmp_path), "on"])
    monkeypatch.setattr(server, "load_config", lambda root: {"url": URL, "ngrok": "/bin/ngrok"})
    monkeypatch.setattr(
        server.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    )
    monkeypatch.setattr(server, "systemctl", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(server, "wait_ready", lambda root, url: calls.append(("ready", root, url)))
    server.main()
    assert calls == [
        ("enable", server.TARGET),
        ("start", server.TARGET, *server.SERVICES),
        ("ready", tmp_path, URL),
    ]


def test_on_does_not_start_without_ngrok_configuration(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["tacit-server", "--project", str(tmp_path), "on"])
    monkeypatch.setattr(server, "load_config", lambda root: {"url": URL, "ngrok": "/bin/ngrok"})
    monkeypatch.setattr(
        server.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1)
    )
    monkeypatch.setattr(server, "systemctl", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 1
    assert calls == []


def test_relay_client_bypasses_ngrok_browser_interstitial():
    relay = RelayClient(URL, "private-token")
    try:
        assert relay.http.headers["ngrok-skip-browser-warning"] == "1"
        assert relay.http.headers["Authorization"] == "Bearer private-token"
    finally:
        relay.close()
