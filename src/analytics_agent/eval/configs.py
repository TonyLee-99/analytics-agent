"""Named agent configurations used by the ablations. Names appear in result files and the README."""

from __future__ import annotations

from analytics_agent.agent import AgentConfig

CONFIGS: dict[str, AgentConfig] = {
    # (a) BIRD's published fixed prompt, full schema, zero-shot: comparable to the leaderboard baselines
    "a_zeroshot_official": AgentConfig(name="a_zeroshot_official", schema_mode="official"),
    # (a2) our prompt + full schema (isolates "our prompt" from "retrieval")
    "a2_ourprompt_full": AgentConfig(name="a2_ourprompt_full", schema_mode="full"),
    # (b) + schema retrieval (BM25 top-6 tables + FK bridges)
    "b_retrieval": AgentConfig(name="b_retrieval", schema_mode="retrieved", top_k=6),
    # (c) + 3 retrieved verified examples
    "c_fewshot": AgentConfig(name="c_fewshot", schema_mode="retrieved", top_k=6, few_shot=3),
    # (d) + execution-guided self-correction (<= 2 rounds)
    "d_selfcorrect": AgentConfig(name="d_selfcorrect", schema_mode="retrieved", top_k=6, few_shot=3,
                                 max_corrections=2),
    # (e) + self-consistency over 5 samples (vote on execution results)
    "e_sc5": AgentConfig(name="e_sc5", schema_mode="retrieved", top_k=6, few_shot=3, max_corrections=2,
                         n_samples=5),
    # ---- Olist business set: semantic-layer experiment (3 arms) ----
    "olist_raw": AgentConfig(name="olist_raw", schema_mode="full", few_shot=0, max_corrections=2),
    "olist_glossary": AgentConfig(name="olist_glossary", schema_mode="full", max_corrections=2,
                                  semantic_mode="glossary"),
    "olist_semantic": AgentConfig(name="olist_semantic", schema_mode="full", max_corrections=2,
                                  semantic_mode="semantic"),
    # semantic first; non-metric questions routed to olist_glossary (see agent.HybridAgent)
    "olist_hybrid": AgentConfig(name="olist_hybrid", schema_mode="full", max_corrections=2, semantic_mode="semantic"),
}


def get_config(config_name: str, **overrides) -> AgentConfig:
    import dataclasses

    base = CONFIGS[config_name]
    return dataclasses.replace(base, **overrides) if overrides else base
