"""How many retrievable chunks does each meeting actually have?

A meeting with no chunks can never be retrieved, no matter how well it matches.
"""
import json
from collections import Counter

d = json.load(open('fomc_rag_index.json'))
per = Counter(c['m'] for c in d['chunks'])
empty = [m for mi, m in enumerate(d['meetings']) if per[mi] == 0]
print(f"meetings with zero chunks: {len(empty)} / {len(d['meetings'])}")
for m in empty[:30]:
    print(f"  {m['d']}  action={str(m['a']):>5}  minutes={'yes' if m.get('hm') else 'no'}")

print("\nchunk counts for the 2008 crisis window:")
for mi, m in enumerate(d['meetings']):
    if m['d'].startswith('2008-'):
        print(f"  {m['d']}  action={str(m['a']):>5}  chunks={per[mi]:>3}  minutes={'yes' if m.get('hm') else 'no'}")

print("\ndistribution of chunks per meeting:")
print(Counter(per[mi] for mi in range(len(d['meetings']))).most_common(12))
