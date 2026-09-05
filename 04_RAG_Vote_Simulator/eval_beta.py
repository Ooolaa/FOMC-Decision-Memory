"""Focused sweep: top-M meeting pool + partial class-prevalence correction.

weight_class = sum(scores) / class_count**beta ; beta=0 is the raw sum,
beta=1 fully divides out how common the class is in the corpus.
"""
from __future__ import annotations

import json
from collections import Counter

from eval_retriever import GAP, Index

idx_data = json.load(open('fomc_rag_index.json'))
meetings = idx_data['meetings']
chunks = idx_data['chunks']
chunk_meeting = [c['m'] for c in chunks]
actions = [m['a'] for m in meetings]
class_counts = Counter(a for a in actions if a is not None)
index = Index(chunks)
cases = [mi for mi, m in enumerate(meetings) if m['q'] and m['a'] is not None]

K = 40
# Retrieve once per case, then re-pool under each setting.
cache = {}
for mi in cases:
    exclude = set(range(mi - GAP, mi + GAP + 1))
    hits = index.search(meetings[mi]['q'], chunk_meeting, exclude, K)
    best = {}
    for i, s in hits:
        m2 = chunk_meeting[i]
        if s > best.get(m2, 0):
            best[m2] = s
    cache[mi] = sorted(best.items(), key=lambda kv: -kv[1])

print(f"{'beta':>5} {'topM':>5} {'acc':>7} {'balanced':>9}  per-class recall")
for beta in (0, 0.15, 0.25, 0.35, 0.5, 0.75):
    for top_m in (5, 6, 8, 10, 14):
        conf = [[0] * 3 for _ in range(3)]
        for mi in cases:
            w = {-1: 0.0, 0: 0.0, 1: 0.0}
            for m2, s in cache[mi][:top_m]:
                a = actions[m2]
                if a is not None:
                    w[a] += s
            raw = [w[a] / (class_counts[a] ** beta) for a in (-1, 0, 1)]
            total = sum(raw)
            alpha = 0.12 * (total or 1)
            p = [(v + alpha) / (total + 3 * alpha) for v in raw]
            pred = (-1, 0, 1)[p.index(max(p))]
            conf[actions[mi] + 1][pred + 1] += 1
        n = sum(sum(r) for r in conf)
        acc = sum(conf[i][i] for i in range(3)) / n
        rec = [conf[i][i] / sum(conf[i]) if sum(conf[i]) else 0 for i in range(3)]
        print(f"{beta:>5} {top_m:>5} {acc:>7.3f} {sum(rec)/3:>9.3f}  "
              f"cut={rec[0]:.2f} hold={rec[1]:.2f} hike={rec[2]:.2f}")
