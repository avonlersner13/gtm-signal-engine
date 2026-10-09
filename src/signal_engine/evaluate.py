"""Evaluate the ranking against hidden synthetic ground truth.

Reports precision@10/@25, recall@25, lift vs. random (analytic base rate and a seeded
Monte-Carlo shuffle) and per-play hit rates. The data is synthetic and the generator is
designed to make signals *partly* predictive, so these numbers validate the plumbing,
not the real-world weights.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from signal_engine.identity import account_roots
from signal_engine.models import Company, GroundTruth, WeekResult

KS = (10, 25)
SHUFFLES = 500


@dataclass(frozen=True, slots=True)
class PlayHit:
    """Hit rate of accounts whose primary play is ``play_id``."""

    play_id: str
    accounts: int
    hits: int
    metric: str

    @property
    def rate(self) -> float:
        """hits / accounts."""
        return self.hits / self.accounts if self.accounts else 0.0


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Ranking quality metrics."""

    week: str
    accounts: int
    positives: int
    base_rate: float
    precision: dict[int, float]
    recall_25: float
    lift: dict[int, float]
    random_precision: dict[int, float]
    plays: tuple[PlayHit, ...]


def account_labels(
    companies: Sequence[Company], truth: Sequence[GroundTruth]
) -> dict[str, tuple[bool, bool]]:
    """(positive, churned) per account; positive = converted_to_paid or expanded."""
    roots = account_roots(companies)
    labels: dict[str, tuple[bool, bool]] = {}
    for t in truth:
        acct = roots.get(t.company_id, t.company_id)
        pos, churn = labels.get(acct, (False, False))
        labels[acct] = (pos or t.converted_to_paid or t.expanded, churn or t.churned)
    return labels


def _precision(ranked: Sequence[bool], k: int) -> float:
    top = ranked[:k]
    return sum(top) / len(top) if top else 0.0


def evaluate(
    result: WeekResult, companies: Sequence[Company], truth: Sequence[GroundTruth], seed: int
) -> Evaluation:
    """Score the week's ranking against ground truth."""
    labels = account_labels(companies, truth)
    ranked = [labels.get(a.account_id, (False, False))[0] for a in result.accounts]
    positives = sum(ranked)
    base = positives / len(ranked) if ranked else 0.0
    rng = random.Random(seed)
    shuffled = list(ranked)
    random_hits = dict.fromkeys(KS, 0.0)
    for _ in range(SHUFFLES):
        rng.shuffle(shuffled)
        for k in KS:
            random_hits[k] += _precision(shuffled, k)
    precision = {k: _precision(ranked, k) for k in KS}
    return Evaluation(
        week=result.week,
        accounts=len(ranked),
        positives=positives,
        base_rate=base,
        precision=precision,
        recall_25=sum(ranked[:25]) / positives if positives else 0.0,
        lift={k: precision[k] / base if base else 0.0 for k in KS},
        random_precision={k: v / SHUFFLES for k, v in random_hits.items()},
        plays=_play_hits(result, labels),
    )


def _play_hits(result: WeekResult, labels: dict[str, tuple[bool, bool]]) -> tuple[PlayHit, ...]:
    counts: dict[str, list[int]] = {}
    for a in result.accounts:
        if not a.plays:
            continue
        play = a.plays[0].play_id
        pos, churned = labels.get(a.account_id, (False, False))
        hit = churned if play == "churn_risk" else pos
        bucket = counts.setdefault(play, [0, 0])
        bucket[0] += 1
        bucket[1] += int(hit)
    return tuple(
        PlayHit(p, n, h, "churned" if p == "churn_risk" else "converted or expanded")
        for p, (n, h) in sorted(counts.items(), key=lambda kv: (-kv[1][0], kv[0]))
    )


def render_evaluation(ev: Evaluation) -> str:
    """Render evaluation.md."""
    lines = [
        f"# Evaluation: week of {ev.week}",
        "",
        "> **Honest note.** The data is synthetic. The generator gives each company a hidden",
        "> propensity that drives both its future outcome and (with separate noise) the signals",
        "> the engine sees, so signals are *partly* predictive by design. These numbers show the",
        "> pipeline ranks what the generator made predictable; they say nothing about real",
        "> CodSpeed accounts. Learn real weights from closed-won data before trusting them.",
        "",
        f"Accounts ranked: {ev.accounts}. Positives (converted to paid or expanded in the next",
        f"quarter): {ev.positives} (base rate {ev.base_rate:.1%}).",
        "",
        "| Metric | Engine | Random (500 seeded shuffles) | Lift vs random |",
        "|---|---|---|---|",
    ]
    for k in KS:
        lines.append(
            f"| precision@{k} | {ev.precision[k]:.2f} | {ev.random_precision[k]:.2f} |"
            f" {ev.lift[k]:.1f}x |"
        )
    lines += [
        f"| recall@25 | {ev.recall_25:.2f} | {25 / ev.accounts if ev.accounts else 0:.2f} | - |",
        "",
        "## Hit rate by primary play",
        "",
        "| Play | Accounts | Hits | Hit rate | Hit means |",
        "|---|---|---|---|---|",
        *[
            f"| {p.play_id} | {p.accounts} | {p.hits} | {p.rate:.0%} | {p.metric} |"
            for p in ev.plays
        ],
        "",
        f"Base rate for comparison: {ev.base_rate:.0%} of all accounts convert or expand.",
        "",
    ]
    return "\n".join(lines)
