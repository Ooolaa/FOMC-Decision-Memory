"""Does condition-tag re-ranking help the retriever?

BM25 has no sense of direction: a query saying "inflation is well above the
objective" happily matches ZIRP-era minutes that discuss inflation running
*below* it. Each meeting already carries coarse condition tags, so re-rank the
candidate meetings by agreement with the scenario's own tags:

    adjusted = score * (1 + gamma * overlap - delta * contradiction)

overlap/contradiction are counted over tag pairs, normalised by the number of
query tags. Evaluated with the same leave-one-out protocol.
"""
from __future__ import annotations

import json
from collections import Counter

from eval_retriever import GAP, Index

OPPOSED = [('infl_high', 'infl_low'), ('labor_tight', 'labor_soft'), ('growth_solid', 'growth_weak')]
OPPOSITE = {}
for a, b in OPPOSED:
    OPPOSITE[a] = b
    OPPOSITE[b] = a

idx_data = json.load(open('fomc_rag_index.json'))
meetings = idx_data['meetings']
chunks = idx_data['chunks']
chunk_meeting = [c['m'] for c in chunks]
actions = [m['a'] for m in meetings]
tags = [set(m['tag']) for m in meetings]
index = Index(chunks)
cases = [mi for mi, m in enumerate(meetings) if m['q'] and m['a'] is not None]

K = 40
cache = {}
for mi in cases:
    exclude = set(range(mi - GAP, mi + GAP + 1))
    best = {}
    for i, s in index.search(meetings[mi]['q'], chunk_meeting, exclude, K):
        m2 = chunk_meeting[i]
        if s > best.get(m2, 0):
            best[m2] = s
    cache[mi] = best


def adjust(score, qtags, mtags, gamma, delta):
    if not qtags:
        return score
    overlap = len(qtags & mtags)
    contra = sum(1 for t in qtags if OPPOSITE.get(t) in mtags)
    return score * max(0.05, 1 + gamma * overlap / len(qtags) - delta * contra / len(qtags))


print(f"{'gamma':>6} {'delta':>6} {'topM':>5} {'acc':>7} {'balanced':>9}  per-class recall")
for gamma in (0, 0.3, 0.6, 1.0):
    for delta in (0, 0.5, 1.0, 1.5):
        for top_m in (6, 8, 10):
            conf = [[0] * 3 for _ in range(3)]
            for mi in cases:
                qt = tags[mi]
                ranked = sorted(
                    ((m2, adjust(s, qt, tags[m2], gamma, delta)) for m2, s in cache[mi].items() if actions[m2] is not None),
                    key=lambda kv: -kv[1],
                )[:top_m]
                w = {-1: 0.0, 0: 0.0, 1: 0.0}
                for m2, s in ranked:
                    w[actions[m2]] += s
                raw = [w[-1], w[0], w[1]]
                total = sum(raw)
                alpha = 0.12 * (total or 1)
                p = [(v + alpha) / (total + 3 * alpha) for v in raw]
                pred = (-1, 0, 1)[p.index(max(p))]
                conf[actions[mi] + 1][pred + 1] += 1
            n = sum(sum(r) for r in conf)
            acc = sum(conf[i][i] for i in range(3)) / n
            rec = [conf[i][i] / sum(conf[i]) if sum(conf[i]) else 0 for i in range(3)]
            print(f"{gamma:>6} {delta:>6} {top_m:>5} {acc:>7.3f} {sum(rec)/3:>9.3f}  "
                  f"cut={rec[0]:.2f} hold={rec[1]:.2f} hike={rec[2]:.2f}")
