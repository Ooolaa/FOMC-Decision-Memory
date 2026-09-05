"""Tune the retrieval-only predictor against the leave-one-out backtest.

Mirrors the BM25 + analog-prior logic that runs in the browser so variants can
be compared quickly, then the winner is ported back into app_template.html.

Backtest protocol: query = one meeting's economic-assessment text (all rate
decision / voting / implementation sentences already stripped at build time);
the meeting itself and its +/- GAP neighbours are excluded from retrieval;
predict the action by pooling the retrieved meetings' actual decisions.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict

STOP = set((
    'the a an of to in and or for on at by with that this it as is are was were be been from '
    'has have had not but their they its would will which more than also these those such over under about into '
    'committee federal open market reserve percent participants members meeting policy other some most many few '
    'noted said continued remained however while although there here what when who whom been being can could should'
).split())

K1, B = 1.2, 0.75
GAP = 2


def stem(w: str) -> str:
    if len(w) > 5 and w.endswith('ing'):
        return w[:-3]
    if len(w) > 5 and w.endswith('ed'):
        return w[:-2]
    if len(w) > 4 and w.endswith('es'):
        return w[:-2]
    if len(w) > 4 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r'[a-z]+', text.lower()) if len(w) >= 3 and w not in STOP]


class Index:
    def __init__(self, chunks):
        self.tf = []
        self.len = []
        self.df = Counter()
        self.postings = defaultdict(list)
        for i, c in enumerate(chunks):
            terms = tokenize(c['t'])
            counts = Counter(terms)
            self.tf.append(counts)
            self.len.append(len(terms))
            for w in counts:
                self.df[w] += 1
                self.postings[w].append(i)
        self.N = len(chunks)
        self.avg = sum(self.len) / max(1, self.N)

    def search(self, query, chunk_meeting, exclude, limit):
        qc = Counter(tokenize(query))
        scores = defaultdict(float)
        for w, qf in qc.items():
            posting = self.postings.get(w)
            if not posting:
                continue
            df = self.df[w]
            idf = math.log(1 + (self.N - df + 0.5) / (df + 0.5))
            if idf <= 0:
                continue
            qw = 1 + math.log(qf) if qf > 1 else 1
            for i in posting:
                if chunk_meeting[i] in exclude:
                    continue
                f = self.tf[i][w]
                scores[i] += idf * qw * (f * (K1 + 1)) / (f + K1 * (1 - B + B * self.len[i] / self.avg))
        return sorted(scores.items(), key=lambda kv: -kv[1])[:limit]


def prior(hits, chunk_meeting, actions, variant, class_counts, top_meetings):
    """Pool retrieved chunks into a distribution over {cut, hold, hike}."""
    best: dict[int, float] = {}
    for i, s in hits:
        mi = chunk_meeting[i]
        if s > best.get(mi, 0):
            best[mi] = s
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if top_meetings:
        ranked = ranked[:top_meetings]

    w = {-1: 0.0, 0: 0.0, 1: 0.0}
    per_class: dict[int, list[float]] = {-1: [], 0: [], 1: []}
    for mi, s in ranked:
        a = actions[mi]
        if a is None:
            continue
        w[a] += s
        per_class[a].append(s)

    if variant == 'sum':
        raw = [w[-1], w[0], w[1]]
    elif variant == 'prevalence':
        # Divide by how common the class is in the corpus: otherwise "hold",
        # which is 2/3 of all meetings, wins on sheer supply of neighbours.
        raw = [w[a] / max(1, class_counts[a]) for a in (-1, 0, 1)]
    elif variant == 'mean3':
        raw = [sum(sorted(per_class[a], reverse=True)[:3]) / 3 for a in (-1, 0, 1)]
    elif variant == 'mean3_prev':
        raw = [(sum(sorted(per_class[a], reverse=True)[:3]) / 3) / math.sqrt(max(1, class_counts[a])) for a in (-1, 0, 1)]
    elif variant == 'count':
        raw = [float(len(per_class[a])) for a in (-1, 0, 1)]
    elif variant == 'count_prev':
        raw = [len(per_class[a]) / max(1, class_counts[a]) for a in (-1, 0, 1)]
    else:
        raise ValueError(variant)

    total = sum(raw)
    alpha = 0.12 * (total or 1)
    return [(v + alpha) / (total + 3 * alpha) for v in raw]


def main() -> None:
    idx_data = json.load(open('fomc_rag_index.json'))
    meetings = idx_data['meetings']
    chunks = idx_data['chunks']
    chunk_meeting = [c['m'] for c in chunks]
    actions = [m['a'] for m in meetings]
    class_counts = Counter(a for a in actions if a is not None)
    index = Index(chunks)

    cases = [mi for mi, m in enumerate(meetings) if m['q'] and m['a'] is not None]
    majority = max(Counter(actions[mi] for mi in cases).values()) / len(cases)
    print(f"cases={len(cases)}  majority baseline={majority:.3f}  corpus classes={dict(class_counts)}\n")

    print(f"{'variant':<14} {'K':>4} {'topM':>5} {'acc':>7} {'balanced':>9}  per-class recall")
    for variant in ('sum', 'prevalence', 'mean3', 'mean3_prev', 'count', 'count_prev'):
        for k in (24, 40, 60):
            for top_m in (0, 8, 12):
                conf = [[0] * 3 for _ in range(3)]
                for mi in cases:
                    exclude = set(range(mi - GAP, mi + GAP + 1))
                    hits = index.search(meetings[mi]['q'], chunk_meeting, exclude, k)
                    if not hits:
                        continue
                    p = prior(hits, chunk_meeting, actions, variant, class_counts, top_m)
                    pred = (-1, 0, 1)[p.index(max(p))]
                    conf[actions[mi] + 1][pred + 1] += 1
                n = sum(sum(r) for r in conf)
                acc = sum(conf[i][i] for i in range(3)) / n
                recalls = [conf[i][i] / sum(conf[i]) if sum(conf[i]) else 0 for i in range(3)]
                bal = sum(recalls) / 3
                print(f"{variant:<14} {k:>4} {top_m:>5} {acc:>7.3f} {bal:>9.3f}  "
                      f"cut={recalls[0]:.2f} hold={recalls[1]:.2f} hike={recalls[2]:.2f}")


if __name__ == '__main__':
    main()
