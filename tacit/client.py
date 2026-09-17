from urllib.parse import urlparse

import httpx


class RelayClient:
    def __init__(self, url: str, token: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Use HTTPS for a remote relay (HTTP is allowed on localhost)")
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "ngrok-skip-browser-warning": "1"},
            timeout=20,
        )

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def close(self):
        self.http.close()
