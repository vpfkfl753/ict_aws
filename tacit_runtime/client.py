"""ACP v1 client. Authentication remains owned by each native executable."""

import asyncio
import json
import os
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path


class RuntimeFailure(RuntimeError):
    pass


def resolve_executable(backend):
    root = Path(__file__).resolve().parent.parent
    local = root / (
        ".tools/kirocli/bin/kiro-cli"
        if backend == "kiro"
        else ".tools/opencode/node_modules/.bin/opencode"
    )
    return (
        str(local)
        if local.exists()
        else shutil.which("kiro-cli" if backend == "kiro" else "opencode")
    )


@dataclass(frozen=True)
class TurnResult:
    backend: str
    session_id: str
    text: str
    stop_reason: str


class AgentRuntime:
    """One local user, one native agent process, sequential turns per instance.

    Use separate instances for separate conversations/users. This is an adapter,
    not a sandbox or a multi-user HTTP service. Permission requests are rejected;
    the native runtime's already-trusted tools can still execute.
    """

    def __init__(self, backend, workspace, *, model=None, executable=None, timeout=180):
        if backend not in ("kiro", "opencode"):
            raise ValueError("backend must be kiro or opencode")
        self.backend = backend
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        if backend == "opencode" and (not model or not model.startswith("openai/")):
            raise ValueError("OpenCode subscription mode requires an explicit openai/<model>")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.model, self.timeout = model, timeout
        self.executable = executable or resolve_executable(backend)
        if not self.executable:
            raise RuntimeFailure(f"{backend} executable is not installed")
        self.process = None
        self.session_id = None
        self._counter = 0
        self._lock = asyncio.Lock()
        self._chunks = []
        self._output_bytes = 0
        self._collecting = False

    async def __aenter__(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TACIT_", "SLACK_"))
        }
        env["PATH"] = str(Path(self.executable).resolve().parent) + os.pathsep + env.get("PATH", "")
        if self.backend == "opencode":
            auth_root = Path(env.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            try:
                auth = json.loads((auth_root / "opencode/auth.json").read_text())
                if auth.get("openai", {}).get("type") != "oauth":
                    raise ValueError("OAuth required")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise RuntimeFailure(
                    "OpenCode: connect OpenAI with ChatGPT Plus/Pro first"
                ) from exc
            env.pop("OPENAI_API_KEY", None)
            env.pop("OPENCODE_AUTH_JSON", None)
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                {
                    "enabled_providers": ["openai"],
                    "model": self.model,
                    "share": "disabled",
                    "permission": {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"},
                }
            )
            command = [self.executable, "acp", "--pure"]
        else:
            command = [self.executable, "acp", "--trust-tools", "read"]
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=2**20,
        )
        try:
            initialized = await self._rpc(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "tacit", "version": "0.1.0"},
                },
            )
            if initialized.get("protocolVersion") != 1:
                raise RuntimeFailure("Unsupported ACP version")
            session = await self._rpc("session/new", {"cwd": str(self.workspace), "mcpServers": []})
            self.session_id = session["sessionId"]
            if self.model:
                await self._rpc(
                    "session/set_model", {"sessionId": self.session_id, "modelId": self.model}
                )
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        p = self.process
        if p is None:
            return
        self.process = None
        # Clean up native agent children too, including after a timeout.
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(p.wait(), 3)
        except TimeoutError:
            pass
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await p.wait()

    async def prompt(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("prompt must be non-empty text")
        async with self._lock:
            if not self.process or not self.session_id:
                raise RuntimeFailure("Use async with AgentRuntime(...)")
            self._chunks, self._output_bytes, self._collecting = [], 0, True
            try:
                result = await self._rpc(
                    "session/prompt",
                    {
                        "sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": text}],
                    },
                )
                reason = result.get("stopReason")
                if not isinstance(reason, str):
                    raise RuntimeFailure("ACP response has no stopReason")
                return TurnResult(self.backend, self.session_id, "".join(self._chunks), reason)
            except BaseException:
                # A failed/cancelled turn must not leave an agent consuming credits.
                await self.close()
                raise
            finally:
                self._collecting = False

    async def _send(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def _rpc(self, method, params):
        self._counter += 1
        ident = self._counter
        try:
            async with asyncio.timeout(self.timeout):
                await self._send(
                    {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
                )
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        raise RuntimeFailure(f"{self.backend}: ACP process exited during {method}")
                    self._output_bytes += len(line)
                    if self._output_bytes > 16 * 2**20:
                        raise RuntimeFailure("ACP output exceeded 16 MiB")
                    item = json.loads(line)
                    if item.get("method"):
                        await self._handle_agent_message(item)
                    elif item.get("id") == ident:
                        if "error" in item:
                            # Do not propagate arbitrary provider output into Slack/logs.
                            code = item["error"].get("code")
                            raise RuntimeFailure(
                                f"{self.backend}: {method} failed (ACP {code}); check native login/model"
                            )
                        return item["result"]
        except TimeoutError as exc:
            raise RuntimeFailure(f"{self.backend}: {method} timed out") from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeFailure(f"{self.backend}: malformed ACP response") from exc

    async def _handle_agent_message(self, item):
        if "id" in item:
            if item["method"] == "session/request_permission":
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": item["id"],
                        "result": {"outcome": {"outcome": "cancelled"}},
                    }
                )
            else:
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": item["id"],
                        "error": {"code": -32601, "message": "Client capability not supported"},
                    }
                )
            return
        params = item.get("params", {})
        if (
            self._collecting
            and item["method"] == "session/update"
            and params.get("sessionId") == self.session_id
        ):
            update = params.get("update", {})
            content = update.get("content", {})
            if (
                update.get("sessionUpdate") == "agent_message_chunk"
                and content.get("type") == "text"
            ):
                self._chunks.append(content["text"])
