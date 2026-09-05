"""Validate the built index against the frozen R5 action labels."""
import json, datetime, collections, sys

d = json.load(open('fomc_rag_index.json'))
lg = json.load(open('../app/artifacts/reaction/pooled_ordered_logit_v1.json'))
mine = {m['d']: m['a'] for m in d['meetings']}


def near(k):
    y = datetime.date.fromisoformat(k)
    for off in (0, 1, -1, 2, -2, 3, -3):
        k2 = str(y + datetime.timedelta(days=off))
        if k2 in mine:
            return k2
    return None


ok = bad = miss = 0
badlist = []
for mid, a in zip(lg['meeting_ids'], lg['actions']):
    k = near(mid[5:])
    if k is None:
        miss += 1
        continue
    if mine[k] == a - 1:
        ok += 1
    else:
        bad += 1
        badlist.append((mid, a - 1, mine[k]))
print(f"action vs frozen R5: match={ok}/{ok+bad+miss}  mismatch={bad}  not_in_csv={miss}")
print("mismatches:", badlist)
print("action distribution:", collections.Counter(m['a'] for m in d['meetings']))
dirs = collections.Counter(e['dir'] for m in d['members'] for e in m['events'])
print("dissent directions:", dirs)
