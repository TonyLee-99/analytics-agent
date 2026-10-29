"""Calibrated confidence and abstention ("know when not to answer").

A wrong number handed to an executive costs more than "I'm not sure -- here is the SQL, please
review". So the agent attaches a confidence to every answer and abstains (routes to a human analyst,
or escalates to a stronger model) below a threshold.

Signals (all available at answer time, none uses the gold answer):
- sc_agreement      share of N sampled SQLs whose *execution results* agree with the chosen answer
- main_in_majority  whether the default (sample-0) answer is in the majority cluster
- corrections       number of self-correction rounds the default answer needed
- had_problem       the final default answer still had an error/empty/NULL problem
- retrieval_top     BM25 score of the best-matching table (low = question far from the schema)
- n_rows_log        log(1 + rows returned)

Two scorers:
- `agreement`: the raw self-consistency agreement (no fitting at all)
- `logreg`   : logistic regression on the signals, out-of-fold (5-fold CV) so every reported number
               is on data the model did not see
Metrics: risk-coverage curve, EX at fixed coverage, AURC, AUROC, ECE (10 bins), Brier.
"""

from __future__ import annotations

import math
import random

import numpy as np

FEATURES = ["sc_agreement", "main_in_majority", "corrections", "had_problem", "retrieval_top", "n_rows_log"]


def features(rec: dict) -> list[float]:
    s = rec.get("signals", {})
    return [
        float(s.get("sc_agreement") or 0.0),
        float(bool(s.get("main_in_majority", True))),
        float(s.get("corrections") or 0),
        float(bool(s.get("main_problems"))),
        float(s.get("retrieval_top_score") or 0.0),
        math.log1p(float(s.get("n_rows") or 0)),
    ]


def _standardize(X: np.ndarray, mu=None, sd=None):
    mu = X.mean(0) if mu is None else mu
    sd = X.std(0) + 1e-9 if sd is None else sd
    return (X - mu) / sd, mu, sd


def fit_logreg(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 2000, lr: float = 0.1):
    Xs, mu, sd = _standardize(X)
    Xb = np.hstack([Xs, np.ones((len(Xs), 1))])
    w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) / len(y) + l2 * np.r_[w[:-1], 0] / len(y)
        w -= lr * g
    return w, mu, sd


def predict_logreg(model, X: np.ndarray) -> np.ndarray:
    w, mu, sd = model
    Xs, _, _ = _standardize(X, mu, sd)
    return 1 / (1 + np.exp(-np.hstack([Xs, np.ones((len(Xs), 1))]) @ w))


def oof_logreg(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0) -> np.ndarray:
    idx = list(range(len(y)))
    random.Random(seed).shuffle(idx)
    out = np.zeros(len(y))
    for f in range(k):
        test = idx[f::k]
        train = [i for i in idx if i not in set(test)]
        m = fit_logreg(X[train], y[train])
        out[test] = predict_logreg(m, X[test])
    return out


def auroc(conf: np.ndarray, y: np.ndarray) -> float:
    pos, neg = conf[y == 1], conf[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    # probability a random correct answer outranks a random wrong one (ties count half)
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


def ece(conf: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    tot = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if m.any():
            tot += m.mean() * abs(conf[m].mean() - y[m].mean())
    return float(tot)


def risk_coverage(conf: np.ndarray, y: np.ndarray, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Sort by confidence (random tie-break, fixed seed) and report accuracy of the top-c fraction."""
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.random(len(conf)), -conf))
    ys = y[order]
    cov = np.arange(1, len(ys) + 1) / len(ys)
    acc = np.cumsum(ys) / np.arange(1, len(ys) + 1)
    return cov, acc


def acc_at(conf: np.ndarray, y: np.ndarray, coverage: float) -> float:
    cov, acc = risk_coverage(conf, y)
    i = max(0, int(math.ceil(coverage * len(y))) - 1)
    return float(acc[i])


def selective_report(conf: np.ndarray, y: np.ndarray) -> dict:
    cov, acc = risk_coverage(conf, y)
    return {
        "n": int(len(y)),
        "ex_at_100": round(100 * float(y.mean()), 2),
        "ex_at_90": round(100 * acc_at(conf, y, 0.9), 2),
        "ex_at_80": round(100 * acc_at(conf, y, 0.8), 2),
        "ex_at_70": round(100 * acc_at(conf, y, 0.7), 2),
        "ex_at_50": round(100 * acc_at(conf, y, 0.5), 2),
        "aurc": round(float(np.mean(1 - acc)), 4),  # area under risk-coverage (lower is better)
        "auroc": round(auroc(conf, y), 4),
        "ece": round(ece(conf, y), 4),
        "brier": round(float(np.mean((conf - y) ** 2)), 4),
    }
