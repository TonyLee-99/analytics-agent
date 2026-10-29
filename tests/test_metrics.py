import numpy as np

from analytics_agent.confidence import auroc, ece, selective_report
from analytics_agent.eval.metrics import bird_ex, business_ex, mcnemar_exact, wilson


def test_bird_ex_matches_official_set_semantics():
    gold = [("a", 1), ("b", 2)]
    assert bird_ex([("b", 2), ("a", 1)], gold) == 1  # row order ignored
    assert bird_ex([("a", 1), ("b", 2), ("a", 1)], gold) == 1  # duplicates ignored (set, like the official script)
    assert bird_ex([(1, "a"), (2, "b")], gold) == 0  # column order matters
    assert bird_ex([("a", 1)], gold) == 0
    assert bird_ex(None, gold) == 0
    assert bird_ex([(0.1 + 0.2,)], [(0.3,)]) == 0  # strict float equality, as in BIRD


def test_business_ex_tolerates_rounding_only():
    assert business_ex([(137.1414,)], [(137.14141414,)]) == 1
    assert business_ex([(137.2,)], [(137.14141414,)]) == 0
    assert business_ex([("SP", 10)], [("SP", 10.0)]) == 1


def test_wilson_and_mcnemar():
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    r = mcnemar_exact([1, 1, 0, 0, 1], [1, 0, 1, 1, 1])
    assert r["only_a"] == 1 and r["only_b"] == 2 and r["p_value"] == 1.0


def test_confidence_metrics_known_cases():
    y = np.array([1, 1, 0, 0])
    assert auroc(np.array([0.9, 0.8, 0.2, 0.1]), y) == 1.0
    assert auroc(np.array([0.1, 0.2, 0.8, 0.9]), y) == 0.0
    assert ece(np.array([1.0, 1.0, 0.0, 0.0]), y) == 0.0
    rep = selective_report(np.array([0.9, 0.8, 0.2, 0.1]), y)
    assert rep["ex_at_50"] == 100.0 and rep["ex_at_100"] == 50.0
