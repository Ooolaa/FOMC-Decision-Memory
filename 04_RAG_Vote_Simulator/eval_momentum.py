"""Validate the momentum features on hand-built historical crisis scenarios.

The leave-one-out backtest cannot measure a change to the scenario query: its
queries are each meeting's own statement text, not a synthesised description.
So query changes are checked here, against dated crisis episodes whose real
outcome is known, using contemporaneous 3-month changes.

Mirrors the JS in app_template.html (scenarioQuery / scenarioTags / analogPrior).
Run after build_rag_index.py; keep in sync with the template.
"""
from __future__ import annotations

import json
import math
import sys

from eval_retriever import Index

OPPOSITE = {
    'infl_high': 'infl_low', 'infl_low': 'infl_high',
    'labor_tight': 'labor_soft', 'labor_soft': 'labor_tight',
    'growth_solid': 'growth_weak', 'growth_weak': 'growth_solid',
    'infl_moderating': 'infl_accelerating', 'infl_accelerating': 'infl_moderating',
    'stress_intensifying': 'stress_easing', 'stress_easing': 'stress_intensifying',
}
MEAN_MID = 1.253099173553719
K, M, BETA, GAMMA, DELTA, W = 400, 8, 0.0, 0.0, 1.5, 0.45
NAMES = ['降息', '維持', '升息']


def scenario_query(x: dict) -> str:
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

    if x['spread'] >= 4.5:
        p.append("severe strains in financial markets, credit markets are not functioning normally, "
                 "extraordinary liquidity measures, acute financial market stress, sharply wider risk spreads, "
                 "impaired credit availability for households and businesses")
    elif x['spread'] >= 3.2:
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

    # momentum
    c3, s3, u3 = x.get('cpi_3m', 0), x.get('spread_3m', 0), x.get('u_3m', 0)
    if c3 <= -0.8:
        p.append("inflationary pressures have started to moderate, energy and other commodity prices have declined "
                 "markedly, inflation expectations are diminishing, the upside risks to price stability have "
                 "diminished, the Committee expects inflation to moderate in coming quarters")
    elif c3 <= -0.3:
        p.append("inflation has moderated somewhat in recent months, price pressures have eased")
    elif c3 >= 0.8:
        p.append("inflation has picked up sharply, price pressures have intensified and broadened, "
                 "upside risks to inflation have increased")
    elif c3 >= 0.3:
        p.append("inflation has moved up in recent months")

    if s3 >= 1.0:
        p.append("the intensification of financial market turmoil, strains in financial markets have increased "
                 "significantly, conditions in financial markets have deteriorated, disruptions in credit markets "
                 "are likely to exert additional restraint on spending, downside risks to growth have increased")
    elif s3 >= 0.4:
        p.append("financial conditions have tightened somewhat, credit spreads have widened in recent months")
    elif s3 <= -0.6:
        p.append("strains in financial markets have eased, functioning of credit markets has improved, "
                 "financial conditions have become more supportive")

    if u3 >= 0.4:
        p.append("labor market conditions have deteriorated rapidly, job losses have mounted, "
                 "payroll employment has declined further")
    elif u3 <= -0.3:
        p.append("labor market conditions have improved further, the unemployment rate has declined in recent months")
    return ' '.join(p)


def scenario_tags(x: dict) -> list[str]:
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
    c3, s3, u3 = x.get('cpi_3m', 0), x.get('spread_3m', 0), x.get('u_3m', 0)
    if c3 <= -0.5:
        t.append('infl_moderating')
    elif c3 >= 0.5:
        t.append('infl_accelerating')
    if s3 >= 0.8:
        t.append('stress_intensifying')
    elif s3 <= -0.6:
        t.append('stress_easing')
    if u3 >= 0.4:
        t.append('labor_deteriorating')
    return t


def main() -> int:
    d = json.load(open('fomc_rag_index.json'))
    meetings, chunks = d['meetings'], d['chunks']
    cm = [c['m'] for c in chunks]
    index = Index(chunks)
    lg = json.load(open('../02_Application/artifacts/reaction/pooled_ordered_logit_v1.json'))
    sig = lambda v: 1 / (1 + math.exp(-v))

    def logit(x):
        f = {'cpi_yoy': x['cpi'], 'unemployment_level': x['u'], 'unemployment_12m_change': x['du'],
             'payroll_yoy': x['pay'], 'credit_spread_baa10y': x['spread'],
             'yield_curve_10y_2y': x['curve'], 'policy_midpoint': x['mid']}
        eta = sum((f[k] - lg['means'][k]) / lg['scales'][k] * lg['coefficients'][k] for k in lg['features'])
        c0, c1 = lg['cutpoints']
        return [sig(c0 - eta), sig(c1 - eta) - sig(c0 - eta), 1 - sig(c1 - eta)]

    def rag(x, use_momentum=True):
        xx = dict(x)
        if not use_momentum:
            xx['cpi_3m'] = xx['spread_3m'] = xx['u_3m'] = 0
        q, qt = scenario_query(xx), scenario_tags(xx)
        best = {}
        for i, s in index.search(q, cm, set(), K):
            m2 = cm[i]
            if s > best.get(m2, 0):
                best[m2] = s
        ranked = []
        for m2, s in best.items():
            if meetings[m2]['a'] is None:
                continue
            tags = meetings[m2]['tag'] or []
            co = sum(1 for t in qt if OPPOSITE.get(t) in tags)
            ov = sum(1 for t in qt if t in tags)
            adj = max(0.05, 1 + GAMMA * ov / len(qt) - DELTA * co / len(qt)) if qt else 1
            ranked.append((s * adj, m2))
        ranked.sort(reverse=True)
        w = {-1: 0.0, 0: 0.0, 1: 0.0}
        for s, m2 in ranked[:M]:
            w[meetings[m2]['a']] += s
        raw = [w[-1], w[0], w[1]]
        total = sum(raw)
        a = 0.12 * (total or 1)
        p = [(v + a) / (total + 3 * a) for v in raw]
        return p, [(meetings[m2]['d'], meetings[m2]['a']) for _, m2 in ranked[:M]]

    def blend(pm, pr):
        o = [pm[i] ** (1 - W) * pr[i] ** W for i in range(3)]
        s = sum(o)
        return [v / s for v in o]

    SCEN = [
        ("2008-09-16 雷曼隔天", dict(cpi=4.9, u=6.1, du=1.4, pay=-0.8, spread=3.45, curve=1.6, mid=2.0,
                                 cpi_3m=-0.1, spread_3m=0.5, u_3m=0.5), "維持 8-0"),
        ("2008-10-29 二度降息", dict(cpi=3.7, u=6.5, du=1.8, pay=-1.2, spread=5.0, curve=1.9, mid=1.5,
                                 cpi_3m=-1.9, spread_3m=2.0, u_3m=0.9), "降息 50bp"),
        ("2008-12-16 降至零利率", dict(cpi=0.1, u=7.3, du=2.4, pay=-2.2, spread=6.0, curve=1.4, mid=1.0,
                                  cpi_3m=-4.8, spread_3m=2.5, u_3m=1.2), "降息至 0-0.25%"),
        ("2020-01-29 COVID 前", dict(cpi=2.3, u=3.5, du=-0.3, pay=1.4, spread=2.1, curve=0.18, mid=1.625,
                                    cpi_3m=0.2, spread_3m=0.1, u_3m=0.0), "維持"),
        ("2020-03-15 緊急降息", dict(cpi=1.5, u=3.5, du=-0.3, pay=1.4, spread=4.5, curve=0.6, mid=1.125,
                                  cpi_3m=-0.8, spread_3m=2.4, u_3m=0.0), "降息 100bp 至 0-0.25%"),
        ("2022-06-15 升息 75bp", dict(cpi=8.6, u=3.6, du=-2.3, pay=4.3, spread=2.3, curve=0.1, mid=0.875,
                                    cpi_3m=0.9, spread_3m=0.4, u_3m=-0.2), "升息 75bp"),
    ]
    hit_new = hit_old = 0
    for name, x, truth in SCEN:
        pm = logit(x)
        pr_new, top_new = rag(x, True)
        pr_old, _ = rag(x, False)
        pb_new, pb_old = blend(pm, pr_new), blend(pm, pr_old)
        want = 0 if '維持' in truth else (-1 if '降息' in truth else 1)
        got_new = [-1, 0, 1][pb_new.index(max(pb_new))]
        got_old = [-1, 0, 1][pb_old.index(max(pb_old))]
        hit_new += got_new == want
        hit_old += got_old == want
        print(f"\n{'='*78}\n{name}   實際：{truth}")
        print(f"  計量模型（不變）  降息 {pm[0]:5.1%}  維持 {pm[1]:5.1%}  升息 {pm[2]:5.1%}")
        print(f"  檢索・無動量      降息 {pr_old[0]:5.1%}  維持 {pr_old[1]:5.1%}  升息 {pr_old[2]:5.1%}"
              f"   -> 混合 {NAMES[got_old + 1]} {'✓' if got_old == want else '✗'}")
        print(f"  檢索・有動量      降息 {pr_new[0]:5.1%}  維持 {pr_new[1]:5.1%}  升息 {pr_new[2]:5.1%}"
              f"   -> 混合 {NAMES[got_new + 1]} {'✓' if got_new == want else '✗'}")
        print(f"  類比: {' '.join(dd + '(' + NAMES[a + 1][0] + ')' for dd, a in top_new)}")
    print(f"\n{'='*78}\n六個歷史案例：加動量前 {hit_old}/6 正確，加動量後 {hit_new}/6 正確")
    return 0


if __name__ == '__main__':
    sys.exit(main())
