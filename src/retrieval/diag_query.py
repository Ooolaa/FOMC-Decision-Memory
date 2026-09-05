"""Why doesn't a 2008-crisis scenario retrieve the 2008 crisis meetings?

Reproduces the app's scenarioQuery() for a given set of inputs, runs the same
BM25 + tag penalty, and prints where each era lands.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

from eval_retriever import Index

OPPOSITE = {
    'infl_high': 'infl_low', 'infl_low': 'infl_high',
    'labor_tight': 'labor_soft', 'labor_soft': 'labor_tight',
    'growth_solid': 'growth_weak', 'growth_weak': 'growth_solid',
}
MEAN_MID = 1.253099173553719


def scenario_query(x):
    p = []
    if x['cpi'] >= 3.5:
        p.append("inflation remains elevated well above the Committee's 2 percent longer-run objective, "
                 "price pressures are broad based, upside risks to inflation, inflation expectations")
    elif x['cpi'] >= 2.5:
        p.append("inflation remains somewhat elevated relative to the Committee's 2 percent objective, "
                 "inflation has moderated but remains above goal")
    elif x['cpi'] >= 1.6:
        p.append("inflation is running close to the Committee's 2 percent longer-run objective, price stability")
    else:
        p.append("inflation has continued to run below the Committee's longer-run objective, disinflation, "
                 "subdued price pressures, inflation compensation declined")

    if x['u'] >= 6.5:
        p.append("unemployment rate is elevated, substantial slack, underutilization of labor resources, "
                 "weak labor market conditions")
    elif x['u'] >= 5:
        p.append("unemployment rate remains somewhat above its longer-run normal level, labor market slack remains")
    else:
        p.append("unemployment rate is low, labor market conditions remain tight, maximum employment")

    if x['du'] >= 0.5:
        p.append("unemployment rate has moved up notably, job gains have slowed, "
                 "deterioration in labor market conditions, rising layoffs")
    elif x['du'] >= 0.2:
        p.append("unemployment rate has edged up, job gains have moderated")
    elif x['du'] <= -0.3:
        p.append("unemployment rate has declined further, labor market has strengthened")

    if x['pay'] <= -0.5:
        p.append("payroll employment has declined, job losses, contraction in economic activity, recession")
    elif x['pay'] <= 0.5:
        p.append("job gains have slowed, employment growth has been modest")
    elif x['pay'] >= 2:
        p.append("job gains have been strong, robust payroll growth, solid expansion")

    if x['spread'] >= 3.2:
        p.append("strains in financial markets, credit spreads have widened substantially, "
                 "tighter credit conditions, financial market stress, disruptions in credit markets")
    elif x['spread'] >= 2.3:
        p.append("credit spreads have widened somewhat, financial conditions have tightened")
    else:
        p.append("financial conditions remain accommodative, credit spreads are narrow, risk appetite")

    if x['curve'] <= -0.3:
        p.append("the yield curve is inverted, market participants expect policy easing, term premium, "
                 "restrictive policy stance")
    elif x['curve'] >= 1.5:
        p.append("the yield curve has steepened, longer-term yields have risen")

    if x['mid'] >= MEAN_MID + 1.5:
        p.append("the current stance of monetary policy is restrictive, policy is well into restrictive territory, "
                 "cumulative tightening")
    elif x['mid'] <= 0.5:
        p.append("the federal funds rate is at the effective lower bound, policy accommodation, balance sheet")
    return ' '.join(p)


def scenario_tags(x):
    t = []
    if x['cpi'] >= 2.5:
        t.append('infl_high')
    elif x['cpi'] < 1.6:
        t.append('infl_low')
    if x['u'] < 5 and x['du'] <= 0.2:
        t.append('labor_tight')
    if x['du'] >= 0.3 or x['pay'] <= 0:
        t.append('labor_soft')
    if x['pay'] <= -0.5:
        t.append('growth_weak')
    elif x['pay'] >= 1.0:
        t.append('growth_solid')
    if x['spread'] >= 2.6:
        t.append('fin_stress')
    return t


def main():
    d = json.load(open('fomc_rag_index.json'))
    meetings, chunks = d['meetings'], d['chunks']
    chunk_meeting = [c['m'] for c in chunks]
    index = Index(chunks)

    x = {'cpi': 4.9, 'u': 6.1, 'du': 1.4, 'pay': -0.8, 'spread': 3.45, 'curve': 1.6, 'mid': 2.0}
    q, qt = scenario_query(x), scenario_tags(x)
    print('tags:', qt)
    print('query:', q[:300], '...\n')

    hits = index.search(q, chunk_meeting, set(), 400)
    best = {}
    for i, s in hits:
        mi = chunk_meeting[i]
        if s > best.get(mi, 0):
            best[mi] = s
    gamma = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    delta = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    print(f'gamma={gamma} delta={delta}\n')
    ranked = []
    for mi, s in best.items():
        mt = meetings[mi]
        tags = mt['tag'] or []
        contra = sum(1 for t in qt if OPPOSITE.get(t) in tags)
        overlap = sum(1 for t in qt if t in tags)
        adj = max(0.05, 1 + gamma * overlap / len(qt) - delta * contra / len(qt)) if qt else 1
        ranked.append((s * adj, s, contra, mi))
    ranked.sort(reverse=True)

    print(f"{'rank':>4} {'adj':>7} {'raw':>7} {'contra':>6}  date        action  tags")
    for r, (adj, raw, contra, mi) in enumerate(ranked[:12], 1):
        mt = meetings[mi]
        print(f"{r:>4} {adj:>7.2f} {raw:>7.2f} {contra:>6}  {mt['d']}  {str(mt['a']):>5}   {','.join(mt['tag'])}")

    print('\nwhere the 2008 crisis meetings land:')
    pos = {mi: r for r, (_, _, _, mi) in enumerate(ranked, 1)}
    for mi, mt in enumerate(meetings):
        if mt['d'].startswith('2008-') or mt['d'].startswith('2009-0'):
            r = pos.get(mi)
            row = next((x for x in ranked if x[3] == mi), None)
            if row:
                print(f"  {mt['d']}  action={str(mt['a']):>5}  rank={r:>4}  adj={row[0]:6.2f} raw={row[1]:6.2f} "
                      f"contra={row[2]}  tags={','.join(mt['tag'])}")
            else:
                print(f"  {mt['d']}  action={str(mt['a']):>5}  NOT RETRIEVED  tags={','.join(mt['tag'])}")


if __name__ == '__main__':
    sys.exit(main())
