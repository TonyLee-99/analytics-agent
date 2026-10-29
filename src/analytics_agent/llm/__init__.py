"""LLM backends behind one interface (see base.LLMClient)."""

from analytics_agent.llm.base import LLMClient, LLMError, LLMResponse


def make_client(name: str):
    """'haiku' | 'sonnet' | 'opus' | 'qwen' | full model id."""
    from analytics_agent.llm.claude_cli import ClaudeCLIClient
    from analytics_agent.llm.ollama import OllamaClient

    aliases = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
    if name == "mock":  # offline smoke tests / CI: always answers a trivial, valid query
        from analytics_agent.llm.mock import MockLLM

        return MockLLM("Plan: count orders.\n```sql\nSELECT count(*) AS n_orders FROM orders\n```")
    if name in ("qwen", "qwen2.5:7b"):
        return OllamaClient("qwen2.5:7b")
    return ClaudeCLIClient(aliases.get(name, name))


__all__ = ["LLMClient", "LLMError", "LLMResponse", "make_client"]
