"""Local, subscription-authenticated agent runtimes for Tacit."""

from .client import AgentRuntime, RuntimeFailure, TurnResult

__all__ = ["AgentRuntime", "RuntimeFailure", "TurnResult"]
