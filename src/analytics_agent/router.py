"""Cost-aware model routing: answer with the cheap model, escalate to the strong one only when the
cheap model's confidence is low. Confidence comes from self-consistency agreement (see confidence.py).

The threshold was fixed before the results were seen (docs/ACCEPTANCE.md, F2): escalate when fewer
than 4 of 5 Haiku samples agree on the execution result (agreement < 0.8).
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics_agent.agent import Agent, AgentAnswer


@dataclass
class RoutedAnswer:
    answer: AgentAnswer
    escalated: bool
    cheap: AgentAnswer
    cost_usd: float
    latency_s: float


class Router:
    def __init__(self, cheap: Agent, strong: Agent, threshold: float = 0.8, signal: str = "sc_agreement"):
        self.cheap, self.strong, self.threshold, self.signal = cheap, strong, threshold, signal

    def confidence(self, ans: AgentAnswer) -> float:
        s = ans.signals
        if self.signal == "sc_agreement":
            return float(s.get("sc_agreement") or 0.0)
        # single-sample fallback: confident only if no problem was seen and no correction was needed
        return 1.0 if (s.get("final_ok") and not s.get("main_problems")) else 0.0

    def ask(self, question: str, db_id: str, evidence: str = "", task_id: str | None = None) -> RoutedAnswer:
        a = self.cheap.ask(question, db_id, evidence, task_id=task_id)
        cost = a.signals["usage"]["cost_usd"]
        lat = a.signals["usage"]["latency_s"]
        if a.status == "answered" and self.confidence(a) >= self.threshold:
            return RoutedAnswer(a, False, a, cost, lat)
        b = self.strong.ask(question, db_id, evidence, task_id=task_id)
        return RoutedAnswer(b, True, a, cost + b.signals["usage"]["cost_usd"], lat + b.signals["usage"]["latency_s"])
