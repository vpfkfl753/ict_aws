import argparse
import asyncio
from dataclasses import asdict
import json
import sys

from .client import AgentRuntime, RuntimeFailure


async def main():
    parser = argparse.ArgumentParser(description="Send stdin to a local Tacit agent runtime")
    parser.add_argument("--backend", required=True, choices=["kiro", "opencode"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model")
    parser.add_argument("--executable")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    prompt = sys.stdin.read()
    async with AgentRuntime(**vars(args)) as agent:
        result = await agent.prompt(prompt)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0 if result.stop_reason == "end_turn" else 2


try:
    sys.exit(asyncio.run(main()))
except (RuntimeFailure, ValueError, OSError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
