#!/usr/bin/env python3
"""Build the offline RAG index for FOMC_RAG_Vote_Simulator.html.

Reads communications.csv (FOMC statements + minutes, 2000-2027) and emits a
single JSON index that the HTML embeds: per-member voting/dissent records,
per-meeting policy actions, and retrievable reasoning passages.

Everything is derived from the corpus; nothing is invented here.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent.parent
CSV_PATH = ROOT / "data" / "communications.csv"
OUT_PATH = Path(__file__).resolve().parent / "fomc_rag_index.json"

# --- text hygiene -----------------------------------------------------------
# The CSV is doubly UTF-8 encoded: an en dash sits in the file as the bytes
# c3 a2 c2 80 c2 93, which decode to "a-circumflex + two C1 control chars".
# Round-tripping through latin-1 undoes each layer; a substitution table cannot,
# because the inner bytes land on unprintable control characters.
PUNCT = {
    "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
}
CONTROL_RE = re.compile(r"[\u0080-\u009f\ufffd]")


# A mojibake run is a Latin-1 lead char followed by continuation chars. Repair
# runs individually: one un-encodable character elsewhere in a 50 KB minute
# would otherwise make a whole-string round trip bail out and leave every dash
# in the document broken.
MOJI_RUN = re.compile(r"[\u00c2-\u00f4][\u0080-\u00bf]{1,3}")


def _repair_run(run: str) -> str:
    try:
        return run.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return run


def _unmojibake(text: str) -> str:
    for _ in range(2):
        repaired = MOJI_RUN.sub(lambda m: _repair_run(m.group(0)), text)
        if repaired == text:
            return text
        text = repaired
    return text


def clean(text: str) -> str:
    if not text:
        return ""
    text = _unmojibake(text)
    for bad, good in PUNCT.items():
        text = text.replace(bad, good)
    text = CONTROL_RE.sub("-", text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()



# --- policy action ----------------------------------------------------------

RANGE_RE = re.compile(
    r"target range for the federal funds rate (?:at|to|of) "
    r"([0-9\-/ ]{1,12}(?:to|-) ?[0-9\-/ ]{1,12}) percent",
    re.I,
)
LEVEL_RE = re.compile(
    r"federal funds rate (?:at|to) an? (?:existing )?level of ([0-9\-/ ]{1,10}) percent", re.I
)
# Anchor the action on the funds-rate sentence that actually announces the
# decision. Anchoring on a verb alone mislabels balance-sheet decisions
# ("decided to increase the size of the balance sheet") as rate hikes, and
# anchoring on the first rate verb mislabels the 2015 holds, whose forward
# guidance ("it will be appropriate to raise the target range") comes first.
RATE_VERB_RE = re.compile(
    r"\b(raise|raised|raising|increase|increased|increasing|"
    r"lower|lowered|lowering|reduce|reduced|reducing|decrease|decreased|"
    r"maintain|maintained|maintaining|keep|keeping|leave|leaving|unchanged|reaffirm\w*)\b",
    re.I,
)
DECIDE_CUE = re.compile(r"\b(decided|voted|agreed|today|established)\b", re.I)
# The vote paragraph quotes the dissenter's preferred action ("preferred to
# maintain the existing target range") and would otherwise outscore the real
# decision sentence.
VOTE_SENT = re.compile(r"\bVoting\b[^.]{0,40}?\b(?:for|against)\b[^.]{0,60}?\b(?:action|statement|policy)\b", re.I)
GUIDANCE_CUE = re.compile(
    r"\b(anticipat\w+|expect\w+|will be appropriate|would be appropriate|in determining|"
    r"future adjustments|when it has seen|is likely|currently expects|judges that it will|"
    r"remains? prepared|stands? ready|assess\w*)\b",
    re.I,
)
HIKE_VERBS = ("rais", "increas")
CUT_VERBS = ("lower", "reduc", "decreas")


def _verb_action(verb: str) -> int:
    verb = verb.lower()
    if verb.startswith(HIKE_VERBS):
        return 1
    if verb.startswith(CUT_VERBS):
        return -1
    return 0


LEVEL_CUE = re.compile(r"\b(?:at|to|of) [0-9][0-9\-/ ]{0,12} ?(?:to [0-9][0-9\-/ ]{0,12} )?percent", re.I)


def action_of(text: str) -> int | None:
    """-1 cut, 0 hold, +1 hike, None unknown.

    Scores every funds-rate sentence rather than taking the first: a single
    2022-style sentence both decides ("decided to raise") and guides
    ("anticipates that ongoing increases will be appropriate"), so the decision
    cue has to outweigh the guidance cue instead of vetoing the sentence.
    """
    best: tuple[int, int] | None = None  # (score, action), earliest wins ties
    for sent in re.split(r"(?<=[.;])\s+", text[:8000]):
        if "federal funds rate" not in sent.lower() or VOTE_SENT.search(sent):
            continue
        verb = RATE_VERB_RE.search(sent)
        if not verb:
            continue
        score = 0
        if DECIDE_CUE.search(sent):
            score += 3
        if GUIDANCE_CUE.search(sent):
            score -= 3
        if LEVEL_CUE.search(sent):
            score += 2
        if best is None or score > best[0]:
            best = (score, _verb_action(verb.group(1)))
    if best is not None:
        return best[1]
    if re.search(r"federal funds rate[^.]{0,80}\b(unchanged|no change|remains appropriate)\b", text[:8000], re.I):
        return 0
    return None


def _num(token: str) -> float | None:
    """Parse Fed rate notation: '3-1/2' -> 3.5, '1/4' -> 0.25, '5' -> 5."""
    token = token.strip().replace(" ", "")
    m = re.fullmatch(r"(?:(\d+)-)?(\d+)/(\d+)", token)
    if m:
        whole = int(m.group(1)) if m.group(1) else 0
        return whole + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"\d+(?:\.\d+)?", token)
    return float(token) if m else None


def target_rate(text: str) -> tuple[str, float | None]:
    """Return (display label, midpoint in percent)."""
    m = RANGE_RE.search(text)
    if m:
        # Ranges are always written "LOW to HIGH"; the hyphens inside a bound
        # are part of the mixed fraction ("3-1/2"), so only " to " splits.
        body = re.sub(r"\s+", " ", m.group(1)).strip()
        parts = [p.strip() for p in re.split(r"\s+to\s+", body, maxsplit=1)]
        lo = _num(parts[0])
        hi = _num(parts[1]) if len(parts) > 1 else None
        if lo is not None and hi is not None:
            return f"{parts[0]}-{parts[1]}%", (lo + hi) / 2
        return body + "%", lo
    m = LEVEL_RE.search(text)
    if not m:
        m = re.search(r"federal funds rate[^.]{0,60}?to ([0-9]+(?:-[0-9/]+)?(?:/[0-9])?) percent", text, re.I)
    if m:
        return m.group(1).strip() + "%", _num(m.group(1))
    return "", None


# --- condition tags ---------------------------------------------------------
# Coarse labels for the macro state the Committee described, so a retrieved
# meeting can be shown as an analog of the user's scenario rather than just a
# text match.
CONDITION_TAGS = {
    "infl_high": r"inflation (?:remains|is|has been|continues to be) elevated|inflation has (?:risen|increased|picked up)|"
                 r"elevated inflation|inflation pressures|heightened inflation|inflation .{0,30}above",
    "infl_low": r"inflation (?:has (?:continued to run|been running|declined)|remains) (?:below|low)|"
                r"below the Committee's (?:longer-run )?(?:objective|goal)|disinflation|subdued inflation",
    "labor_tight": r"job gains (?:have been|were) (?:strong|solid|robust)|labor market (?:conditions )?(?:have )?"
                   r"(?:strengthen|tighten)|unemployment rate has (?:declined|fallen)|labor market remains (?:strong|solid|tight)",
    "labor_soft": r"job gains have (?:slowed|moderated)|unemployment rate has (?:risen|moved up|increased)|"
                  r"labor market (?:has )?(?:softened|weakened)|deterioration in the labor market|job losses",
    "fin_stress": r"strains in financial markets|financial conditions have tightened|disruptions? in (?:financial|credit)|"
                  r"tighter credit conditions|financial market (?:stress|turmoil|volatility)",
    "growth_weak": r"economic activity (?:has )?(?:slowed|weakened|contracted|declined)|economic growth has slowed|"
                   r"recession|activity remains weak",
    "growth_solid": r"expanding at a (?:solid|moderate|strong) pace|economic activity has been expanding|"
                    r"activity (?:has )?(?:strengthened|picked up)",
    # --- momentum tags: the DIRECTION conditions are moving, not their level.
    # The level tags above cannot tell "inflation is high" from "inflation is
    # high but collapsing", yet that distinction is exactly what the Oct-2008
    # statements cite as the reason for cutting.
    "infl_moderating": r"inflation(?:ary pressures)?[^.]{0,60}(?:have|has)?[^.]{0,20}\b(?:moderat|eas|declin|slow|come down|abat)"
                       r"|declines? in the prices of energy|energy (?:and other commodity )?prices have (?:declined|fallen)"
                       r"|expects? inflation to moderate|diminish\w*[^.]{0,50}upside risks to price stability"
                       r"|inflation compensation (?:has )?declined|reduction in inflationary pressures",
    "infl_accelerating": r"inflation has (?:risen|increased|picked up|moved up|been higher)"
                         r"|price pressures have (?:intensified|broadened|increased)"
                         r"|inflation[^.]{0,40}higher than (?:expected|anticipated)"
                         r"|increases? in the prices of energy|upside risks to inflation have increased",
    "stress_intensifying": r"intensification of (?:the )?financial (?:market )?(?:turmoil|crisis|strains)"
                           r"|strains in financial markets have (?:increased|intensified)"
                           r"|financial (?:market )?(?:turmoil|strains|stress)[^.]{0,40}(?:intensified|increased|worsened)"
                           r"|conditions in financial markets have deteriorated"
                           r"|further (?:deterioration|disruption) in financial",
    "stress_easing": r"strains in financial markets have (?:eased|abated|diminished)"
                     r"|financial (?:market )?conditions have (?:improved|eased)"
                     r"|functioning of (?:financial|credit) markets has improved",
    "labor_deteriorating": r"labor market(?:s)? (?:have|has) weakened further|job losses have (?:mounted|increased)"
                           r"|employment has (?:declined|fallen)[^.]{0,40}(?:sharply|substantially|further)"
                           r"|unemployment rate has risen (?:sharply|substantially|notably)"
                           r"|payroll employment (?:has )?(?:declined|fell)",
}
CONDITION_TAGS_C = {k: re.compile(v, re.I) for k, v in CONDITION_TAGS.items()}


def condition_tags(text: str) -> list[str]:
    return [k for k, pat in CONDITION_TAGS_C.items() if pat.search(text)]


# --- roster / vote extraction ----------------------------------------------

NAME_RE = re.compile(r"[A-Z][a-zA-Z'é\-]+(?: [A-Z]\.)*(?: [A-Z][a-zA-Z'é\-]+)+")
TITLES = re.compile(
    r",? ?(Chairman|Chairwoman|Chair|Vice Chair(?:man)?(?: for Supervision)?|Acting Chair(?:man)?)\b",
    re.I,
)


def split_names(blob: str) -> list[str]:
    blob = TITLES.sub("", blob)
    blob = re.sub(r"\band\b", ";", blob)
    out = []
    for part in re.split(r"[;,]", blob):
        part = clean(part).strip(" .")
        if not part or len(part) > 42:
            continue
        if NAME_RE.fullmatch(part):
            out.append(part)
    return out


# The vote lists are single paragraphs whose names carry middle initials, so a
# sentence-splitting regex mis-terminates on "Jerome H. Powell". Slice by
# landmark instead: start after the "were/was/:" lead-in, stop at the next
# landmark phrase.
VOTE_FOR_START = re.compile(r"Voting for [^:.]{0,80}?(?:were|was|:)\s*", re.I)
VOTE_AGAINST_START = re.compile(r"Voting against [^:.]{0,80}?(?:were|was|:)\s*", re.I)
FOR_END = re.compile(
    r"(Voting against|In a related action|Implementation Note|For media inquiries|"
    r"Absent and not voting|voted as an alternate|\n)",
    re.I,
)
AGAINST_END = re.compile(
    r"(who (?:preferred|would have|judged|believed|favored)|each of whom|"
    r"(?:both|all) of whom|preferr?(?:ed|ing)|because|in light of|"
    r"Implementation Note|For media inquiries|Absent and not voting|\n)",
    re.I,
)
DISSENT_CTX_RE = re.compile(r"Voting against.{0,700}?(?:\n|$)", re.S)
TALLY_RE = re.compile(r"by an? (\d{1,2})\s*[-\u2013]\s*(\d{1,2}) vote", re.I)


def slice_vote_list(text: str, start_re: re.Pattern[str], end_re: re.Pattern[str]) -> str:
    m = start_re.search(text)
    if not m:
        return ""
    tail = text[m.end(): m.end() + 900]
    stop = end_re.search(tail)
    return tail[: stop.start()] if stop else tail

# A dissent is hawkish or dovish relative to what the Committee did, and the
# statements say so in a handful of recurring formulas. Score both directions
# and take the sign; ties and balance-sheet-only dissents stay "other".
HAWK_CUES = [
    (r"prefer\w*[^.]{0,120}?\b(?:rais\w+|increas\w+)", 3),
    (r"prefer\w*[^.]{0,120}?\bmaintain\w*", 3),
    (r"prefer\w*[^.]{0,120}?\bsmaller (?:reduction|decrease|cut)", 3),
    (r"prefer\w*[^.]{0,120}?\bno (?:change|reduction|cut)", 3),
    (r"\b(?:less|reduc\w+ the) accommodat", 2),
    (r"\b(?:tighten\w*|firmer|more restrictive|greater restraint)\b", 2),
    (r"\bsupported no change\b", 2),
    (r"\binflation (?:risks?|pressures?)[^.]{0,80}\b(?:elevated|upside|persist)", 1),
]
DOVE_CUES = [
    (r"prefer\w*[^.]{0,120}?\b(?:lower\w*|reduc\w+|decreas\w+|cut)", 3),
    (r"prefer\w*[^.]{0,120}?\blarger (?:reduction|decrease|cut)", 3),
    (r"\b(?:additional|more|further|greater) (?:policy )?accommodat", 3),
    (r"\bpremature\b", 2),
    (r"\beasing\b", 2),
    (r"\b(?:unemployment|labor market)[^.]{0,60}\b(?:elevated|weak|deteriorat)", 1),
    (r"\binflation[^.]{0,60}\b(?:below|well below) (?:the )?(?:target|objective|2 percent)", 1),
]
HAWK_CUES_C = [(re.compile(p, re.I), w) for p, w in HAWK_CUES]
DOVE_CUES_C = [(re.compile(p, re.I), w) for p, w in DOVE_CUES]


REASON_CLAUSE = re.compile(r"\b(?:who|whom)\b", re.I)


def member_dissent_ctx(ctx: str, name: str) -> str:
    """Narrow a shared 'Voting against' paragraph to one dissenter's clause.

    Only when the paragraph carries more than one reason clause: dissenters
    with opposite preferences in one sentence would otherwise score as a tie.
    When several dissenters share a single "who preferred ..." clause, that one
    reason belongs to all of them, so the paragraph is left whole.
    """
    if len(REASON_CLAUSE.findall(ctx)) < 2:
        return ctx
    i = ctx.find(name)
    if i < 0:
        return ctx
    tail = ctx[i + len(name):]
    stop = re.search(r"[;,]\s*(?:and\s+)?[A-Z][a-zA-Z'\-]+ (?:[A-Z]\. )?[A-Z][a-zA-Z'\-]+,? who", tail)
    return name + (tail[: stop.start()] if stop else tail[:400])


def dissent_direction(ctx: str, action: int | None) -> str:
    h = sum(w for pat, w in HAWK_CUES_C if pat.search(ctx))
    d = sum(w for pat, w in DOVE_CUES_C if pat.search(ctx))
    if h > d:
        return "hawk"
    if d > h:
        return "dove"
    return "other"


def slug(name: str) -> str:
    return re.sub(r"[^a-z]+", "-", name.lower()).strip("-")


# --- minutes chunking -------------------------------------------------------

QUANT = re.compile(
    r"^(?:A few|A couple of|A number of|A majority of|A significant number of|Several|Some|Many|"
    r"Most|All|Almost all|Nearly all|Various|One|Two|Participants|Members|Policymakers|"
    r"The Committee members|In (?:their|the) discussion)\b"
    r"[^.]{0,70}?\b(participants|members|policymakers|officials)\b",
    re.I,
)
POLICY_CUE = re.compile(
    r"\b(target range|federal funds rate|policy stance|monetary policy|inflation|labor market|"
    r"unemployment|financial conditions|credit|price stability|dual mandate|balance sheet|"
    r"dissent|restrictive|accommodat)\b",
    re.I,
)
SECTION_KEEP = re.compile(
    r"(Participants. Views|Committee Policy Action|Members. Views|Monetary Policy Options|"
    r"Review of Monetary Policy Strategy|Discussion of )",
    re.I,
)
SECTION_DROP = re.compile(
    r"(Staff Review|Staff Economic Outlook|Developments in Financial Markets|Attendance|"
    r"Approval of Minutes|Annual Organizational|Election of|By unanimous vote|Notation Vote)",
    re.I,
)

MAX_CHUNK = 1400
MAX_CHUNKS_PER_MINUTE = 13
MIN_CHUNKS_PER_MINUTE = 10


def pick_minute_chunks(text: str) -> list[tuple[str, int]]:
    """Return (chunk_text, source_kind); kind 1 = participants' views, 2 = policy action.

    The primary rules key off conventions the minutes only adopted around 2009:
    the "Participants' Views on Current Conditions" heading and the
    "A few participants..." quantifier openings. Selecting on those alone gave
    2012-2026 meetings 17-19 chunks and 2000-2009 meetings 1-6, so the whole
    pre-crisis era -- which holds most of the cuts -- could almost never win a
    place in the top-M analog pool. Anything short of the minimum is therefore
    backfilled with the most policy-dense paragraphs in the document.
    """
    section = ""
    scored: list[tuple[str, int, float]] = []
    spare: list[tuple[str, int, float]] = []
    for line in text.split("\n"):
        s = clean(line)
        if not s:
            continue
        if len(s) < 90 and not s.endswith(".") and s[:1].isupper():
            section = s
            continue
        if len(s) < 220 or SECTION_DROP.search(section):
            continue
        in_views = bool(SECTION_KEEP.search(section))
        quant = bool(QUANT.match(s))
        cues = len(POLICY_CUE.findall(s))
        kind = 2 if re.search(r"Committee Policy Action|Members", section, re.I) else 1
        if quant or (in_views and cues):
            score = (3 if quant else 0) + (2 if in_views else 0) + (1 if cues else 0)
            scored.append((s[:MAX_CHUNK], kind, score))
        elif cues:
            # density, not raw count, so a long rambling paragraph does not
            # outrank a tight policy discussion
            spare.append((s[:MAX_CHUNK], kind, cues / (len(s) / 500)))

    scored.sort(key=lambda t: -t[2])
    picked = [(t, k) for t, k, _ in scored[:MAX_CHUNKS_PER_MINUTE]]
    if len(picked) < MIN_CHUNKS_PER_MINUTE:
        seen = {t for t, _ in picked}
        spare.sort(key=lambda t: -t[2])
        for t, k, _ in spare:
            if t in seen:
                continue
            picked.append((t, k))
            if len(picked) >= MIN_CHUNKS_PER_MINUTE:
                break
    return picked


def statement_paragraphs(text: str) -> list[str]:
    return [clean(p) for p in re.split(r"\n\s*\n|\n(?=[A-Z])", text)]


DECISION_SENT = re.compile(
    r"\b(?:decided|voted|agreed|established|reaffirmed)\b|\bVoting (?:for|against)\b|"
    r"\btarget range for the federal funds rate\b|\bbasis points\b|\bImplementation Note\b|"
    r"\bdiscount rate\b|\bmedia inquiries\b",
    re.I,
)


def condition_query(stmt: str) -> str:
    """The economic assessment only, with every sentence that names the policy
    decision removed.

    Used as the leave-one-out backtest query: leaving the decision sentence in
    would hand the retriever the answer.
    """
    kept = [
        sent for sent in re.split(r"(?<=[.])\s+", stmt)
        if len(sent) > 40 and not DECISION_SENT.search(sent)
    ]
    return " ".join(kept)[:900]


def main() -> int:
    rows = list(csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8", errors="replace")))
    by_date: dict[str, dict] = {}
    for r in rows:
        rec = by_date.setdefault(r["Date"], {"stmt": "", "minute": "", "release": r["Release Date"]})
        text = clean(r["Text"] or "")
        if r["Type"] == "Statement":
            rec["stmt"] = text
        elif r["Type"] == "Minute":
            rec["minute"] = text

    members: dict[str, dict] = {}

    def touch(name: str) -> str:
        key = slug(name)
        if key not in members:
            members[key] = {
                "id": key, "name": name, "votes": 0, "dissents": 0,
                "hawk": 0, "dove": 0, "first": "9999", "last": "0000", "events": [],
            }
        return key

    meetings: list[dict] = []
    chunks: list[dict] = []
    unknown_action: list[str] = []

    for date in sorted(by_date, reverse=True):
        rec = by_date[date]
        stmt, minute = rec["stmt"], rec["minute"]
        if not stmt and not minute:
            continue
        act = action_of(stmt) if stmt else None
        if act is None and minute:
            m = re.search(r"the Committee (?:voted|decided|agreed) to[^.]{0,240}", minute)
            act = action_of(m.group(0)) if m else None
        if act is None:
            unknown_action.append(date)
        mi = len(meetings)

        voters_for: list[str] = []
        voters_against: list[str] = []
        dissent_notes: list[tuple[str, str, str]] = []
        if stmt:
            voters_for = split_names(slice_vote_list(stmt, VOTE_FOR_START, FOR_END))
            voters_against = split_names(slice_vote_list(stmt, VOTE_AGAINST_START, AGAINST_END))
            ctx = DISSENT_CTX_RE.search(stmt)
            ctx_text = clean(ctx.group(0))[:700] if ctx else ""
            for name in voters_against:
                clause = member_dissent_ctx(ctx_text, name)
                dissent_notes.append((name, dissent_direction(clause, act), clause))

        against_set = {slug(n) for n in voters_against}
        for name in voters_for + voters_against:
            key = touch(name)
            m = members[key]
            m["votes"] += 1
            m["first"] = min(m["first"], date)
            m["last"] = max(m["last"], date)
            if key in against_set:
                m["dissents"] += 1
        for name, direction, ctx_text in dissent_notes:
            m = members[touch(name)]
            if direction in ("hawk", "dove"):
                m[direction] += 1
            m["events"].append({"d": date, "dir": direction, "t": ctx_text})

        label, mid = target_rate(stmt) if stmt else ("", None)
        tally = TALLY_RE.search(stmt) if stmt else None
        meetings.append({
            "d": date,
            "a": act,
            "tag": condition_tags(stmt or minute[:12000]),
            "q": condition_query(stmt) if stmt else "",
            "tf": int(tally.group(1)) if tally else None,
            "ta": int(tally.group(2)) if tally else None,
            "r": label,
            "mid": mid,
            "nf": len(voters_for),
            "na": len(voters_against),
            "dis": sorted(against_set),
            "roster": [slug(n) for n in voters_for + voters_against],
            "hm": 1 if minute else 0,
        })

        if stmt:
            for para in statement_paragraphs(stmt):
                # Drop the roster sentences ("Voting for the monetary policy
                # action were ..."). They name every member present without
                # saying anything about their reasoning, and would otherwise be
                # retrieved as that member's own evidence. The dissenters'
                # reasons are kept separately as source kind 3.
                # Cut at the vote list rather than filtering sentences: the
                # names themselves contain periods ("John C. Williams"), so a
                # sentence splitter shreds the roster into fragments that
                # survive the filter.
                cut = VOTE_SENT.search(para)
                if cut:
                    para = para[:cut.start()].strip()
                if len(para) < 180 or not POLICY_CUE.search(para):
                    continue
                chunks.append({"m": mi, "s": 0, "t": para[:MAX_CHUNK]})
        if minute:
            for text, kind in pick_minute_chunks(minute):
                chunks.append({"m": mi, "s": kind, "t": text})
        # Dissenters who share one "who preferred ..." clause share one chunk;
        # emitting it per dissenter would duplicate the passage in evidence.
        shared: dict[str, list[str]] = {}
        for name, direction, ctx_text in dissent_notes:
            if ctx_text:
                shared.setdefault(ctx_text, []).append(slug(name))
        for ctx_text, ids in shared.items():
            chunks.append({"m": mi, "s": 3, "t": ctx_text, "who": ids})

    # Meetings are appended newest-first; walk oldest-first to fill any action
    # the statement language did not settle, by comparing the announced target
    # level with the level standing before it (this is what resolves the
    # Dec-2008 "establish a target range of 0 to 1/4 percent" move).
    prev_mid = None
    for mt in reversed(meetings):
        if mt["a"] is None and mt["mid"] is not None and prev_mid is not None:
            delta = mt["mid"] - prev_mid
            if abs(delta) >= 0.05:
                mt["a"] = 1 if delta > 0 else -1
                mt["a_src"] = "level_delta"
        mt["mid_c"] = mt["mid"] if mt["mid"] is not None else prev_mid
        if mt["mid"] is not None:
            prev_mid = mt["mid"]

    # Attribute passages to members by surname mention, so member evidence is citable.
    surnames: dict[str, str] = {}
    for key, m in members.items():
        if m["votes"] >= 2:
            surnames.setdefault(m["name"].split()[-1], key)
    surname_re = {sur: re.compile(r"\b" + re.escape(sur) + r"\b") for sur in surnames}
    for c in chunks:
        hits = [key for sur, key in surnames.items() if surname_re[sur].search(c["t"])]
        if hits:
            c["n"] = hits[:6]

    # Per-member chunk lists: a member's own evidence is far too sparse to
    # surface through a global top-K search (minutes anonymise participants as
    # "several participants"), so score their own passages directly.
    member_chunks: dict[str, list[int]] = {}
    for ci, c in enumerate(chunks):
        for key in set(c.get("n", []) + list(c.get("who") or [])):
            member_chunks.setdefault(key, []).append(ci)

    member_list = sorted(
        (m for m in members.values() if m["votes"] >= 1),
        key=lambda m: (m["last"], m["votes"]),
        reverse=True,
    )
    for m in member_list:
        m["events"] = m["events"][:6]
        m["ch"] = member_chunks.get(m["id"], [])[:40]

    index = {
        "meta": {
            "source": "communications.csv",
            "rows": len(rows),
            "meetings": len(meetings),
            "chunks": len(chunks),
            "members": len(member_list),
            "date_min": min(m["d"] for m in meetings),
            "date_max": max(m["d"] for m in meetings),
            "unknown_action": unknown_action,
        },
        "members": member_list,
        "meetings": meetings,
        "chunks": chunks,
    }
    OUT_PATH.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta = dict(index["meta"])
    meta.pop("unknown_action")
    print(json.dumps(meta, indent=1))
    print("unknown action meetings:", len(unknown_action), unknown_action[:12])
    print("size MB:", round(OUT_PATH.stat().st_size / 1e6, 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
