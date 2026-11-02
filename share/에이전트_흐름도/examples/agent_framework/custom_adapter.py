"""Team-owned adapter composition example; no network or database calls.

Use this factory's module path with the framework CLI after implementing the
methods below. As shipped, every unimplemented role raises ADAPTER_NOT_CONFIGURED.
It never substitutes synthetic observations for unavailable real services.
"""
from backend.agent_framework.adapters.templates import QwenAdapterStub
from backend.agent_framework.ports import Agents


class TeamQwenAdapter(QwenAdapterStub):
    """Override extract/inspect/report with your team's configured implementation.

    Each method accepts/returns the typed contract from contracts.py. Resolve
    authorized file references in your application, keep raw provider responses
    outside this framework, and validate source IDs before returning a result.
    Translate an ambiguous remote outcome to AdapterFailure(outcome_unknown=True).
    Do not retry or fall back to synthetic data inside these methods.
    """


def make_agents() -> Agents:
    from backend.agent_framework.validation import ReferenceValidator

    qwen = TeamQwenAdapter()
    return Agents(
        extractor=qwen, inspector=qwen,
        validator=ReferenceValidator(), reporter=qwen,
    )

