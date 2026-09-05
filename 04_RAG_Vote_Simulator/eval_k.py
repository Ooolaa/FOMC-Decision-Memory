"""Does the tag re-ranking need a bigger candidate pool?

Re-ranking can only reorder what BM25 already surfaced. With K=40 chunks the
meeting pool is ~25 meetings, all of them lexically modern, so a crisis-era
meeting never gets the chance to be boosted. Sweeps K with the tuned
gamma/delta/M.
"""
from __future__ import annotations

import json
from collections import Counter

from eval_retriever import GAP, Index

OPPOSITE = {
    'infl_high': 'infl_low', 'infl_low': 'infl_high',
    'labor_tight': 'labor_soft', 'labor_soft': 'labor_tight',
    'growth_solid': 'growth_weak', 'growth_weak': 'growth_solid',
}

d = json.load(open('fomc_rag_index.json'))
meetings, chunks = d['meetings'], d['chunks']
chunk_meeting = [c['m'] for c in chunks]
actions = [m['a'] for m in meetings]
tags = [set(m['tag']) for m in meetings]
index = Index(chunks)
cases = [mi for mi, m in enumerate(meetings) if m['q'] and m['a'] is not None]

print(f"{'K':>5} {'gamma':>6} {'delta':>6} {'topM':>5} {'acc':>7} {'balanced':>9}  per-class recall")
for K in (40, 100, 200, 400):
    cache = {}
    for mi in cases:
        exclude = set(range(mi - GAP, mi + GAP + 1))
        best = {}
        for i, s in index.search(meetings[mi]['q'], chunk_meeting, exclude, K):
            m2 = chunk_meeting[i]
            if s > best.get(m2, 0):
                best[m2] = s
        cache[mi] = best
    for gamma, delta in ((0.6, 1.5), (0, 1.5), (0.6, 1.0)):
        for top_m in (6, 8, 10):
            conf = [[0] * 3 for _ in range(3)]
            for mi in cases:
                qt = tags[mi]
                ranked = []
                for m2, s in cache[mi].items():
                    if actions[m2] is None:
                        continue
                    if qt:
                        overlap = len(qt & tags[m2])
                        contra = sum(1 for t in qt if OPPOSITE.get(t) in tags[m2])
                        adj = max(0.05, 1 + gamma * overlap / len(qt) - delta * contra / len(qt))
                    else:
                        adj = 1
                    ranked.append((s * adj, m2))
                ranked.sort(reverse=True)
                w = {-1: 0.0, 0: 0.0, 1: 0.0}
                for s, m2 in ranked[:top_m]:
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
            print(f"{K:>5} {gamma:>6} {delta:>6} {top_m:>5} {acc:>7.3f} {sum(rec)/3:>9.3f}  "
                  f"cut={rec[0]:.2f} hold={rec[1]:.2f} hike={rec[2]:.2f}")
