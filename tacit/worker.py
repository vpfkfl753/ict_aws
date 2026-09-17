import json
import logging
import time
from pathlib import Path

from slack_sdk import WebClient

from tacit.provider import interpret_prompt, prepare_prompt

log = logging.getLogger(__name__)


class SlackSender:
    def __init__(self, token, owner, team):
        self.client = WebClient(token=token, timeout=20)
        auth = self.client.auth_test()
        if auth["user_id"] != owner or auth["team_id"] != team or auth.get("bot_id"):
            raise ValueError("SLACK_USER_TOKEN must belong to this user in this workspace")

    def send(self, exchange):
        channel = self.client.conversations_open(users=exchange["recipient"])["channel"]["id"]
        result = self.client.chat_postMessage(
            channel=channel,
            text=exchange["text"],
            client_msg_id=exchange["id"],
            unfurl_links=False,
            unfurl_media=False,
        )
        return {"channel": result["channel"], "ts": result["ts"]}


class Worker:
    def __init__(self, relay, provider, sender, state_dir: Path):
        self.relay = relay
        self.provider = provider
        self.sender = sender
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

    def cached(self, exchange, key, produce):
        # A result can be replayed after a relay disconnect without calling the model again.
        path = self.state_dir / f"{exchange['id']}-{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        value = produce()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return value

    def update(self, exchange, action, value):
        return self.relay.request(
            "POST",
            f"/v1/work/{exchange['id']}",
            json={"lease": exchange["lease"], "action": action, "value": value},
        )

    def once(self):
        exchange = self.relay.request("POST", "/v1/work/claim")
        if exchange is None:
            return False
        stage = exchange["state"].split("_")[0]
        log.info("exchange=%s stage=%s started", exchange["id"], stage)
        try:
            if stage == "prepare":
                source = exchange["source"] or self.cached(
                    exchange, "source", lambda: self.sender.send(exchange)
                )
                self.update(exchange, "source", source)
                context = self.cached(
                    exchange, "context", lambda: self.provider.run(prepare_prompt(exchange))
                )
                self.update(exchange, "context", context)
            else:
                result = self.cached(
                    exchange, "result", lambda: self.provider.run(interpret_prompt(exchange))
                )
                self.update(exchange, "result", result)
        except Exception as exc:
            # Do not put API exceptions, tokens, prompts or local paths on the wire.
            log.error("exchange=%s stage=%s failed (%s)", exchange["id"], stage, type(exc).__name__)
            self.update(
                exchange, "error", f"{stage}: {type(exc).__name__}. Check the local worker."
            )
        else:
            log.info("exchange=%s stage=%s completed", exchange["id"], stage)
        return True

    def run(self):
        connected = False
        while True:
            try:
                worked = self.once()
                if not connected:
                    log.info("Relay authenticated; worker ready")
                    connected = True
                if not worked:
                    time.sleep(2)
            except Exception as exc:
                connected = False
                log.error("Relay unavailable (%s); retrying in 5 seconds", type(exc).__name__)
                time.sleep(5)
