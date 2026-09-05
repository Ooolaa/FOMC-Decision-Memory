#!/usr/bin/env python3
"""Synthesise the demo narration and derive the whole timeline from it.

Reads VIDEO_SCRIPT_zh-TW.md, splits each timed section's narration into
sentences, renders each one with macOS `say` (zh_TW) and measures it with
`afinfo`. Everything downstream — where the camera moves, when a subtitle
shows, where the audio sits — is computed from those measured lengths, so the
picture can never drift from the voice.

Writes:
    NN_MM.aiff        one clip per sentence
    narration.json    sections, audio clips and subtitle cues, all timed
    subtitles.srt     sidecar captions for the YouTube upload

    python3 build_narration.py            # render + measure
    python3 build_narration.py --rate 185 # slower delivery
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "VIDEO_SCRIPT_zh-TW.md"

GAP = 2.5           # seconds of quiet between sections, for the picture to settle
SUB_MAX = 38        # characters per subtitle cue before it is split in two

# Percent has to move: Chinese says 「百分之七十四點二」, so the marker goes in
# front of the number. Left alone, say trails off with a dangling 「百分之」.
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# `say` reads these the wrong way round, too fast to follow, or not at all.
SPOKEN = [
    ("ordered logit", "歐德 邏吉特模型"),
    ("FRED", "F R E D"),
    ("CPI", "C P I"),
    ("RAG", "R A G"),
    ("Beth M. Hammack", "Hammack"),
    ("Jerome H. Powell", "Powell"),
    ("3,553", "三千五百五十三"),
]


def sections() -> list[tuple[str, str]]:
    """(heading, narration) for each timed section of the script."""
    body = SCRIPT.read_text(encoding="utf-8").split("## YouTube 上傳資料")[0]
    out = []
    for block in re.split(r"\n## ", body)[1:]:
        head = block.split("\n")[0].strip()
        if not re.match(r"\d\d:\d\d", head):
            continue
        lines = [l[2:] for l in block.split("\n")
                 if l.startswith("> ") and not l.startswith("> 正式錄")]
        text = " ".join(lines)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)          # bold markers
        text = text.replace("`", "").strip()
        out.append((head, text))
    return out


def sentences(text: str) -> list[str]:
    """Split on sentence enders, keeping them. One sentence is one audio clip,
    which is what keeps the delivery sounding continuous."""
    return [p.strip() for p in re.split(r"(?<=[。！？])", text) if p.strip()]


def cues(sentence: str, start: float, dur: float) -> list[dict]:
    """Subtitle cues for one spoken sentence.

    A long sentence is broken at a comma or dash near its middle and the time
    shared out by character count — close enough within a single sentence, and
    it keeps every cue down to two readable lines.
    """
    if len(sentence) <= SUB_MAX:
        return [{"start": round(start, 3), "dur": round(dur, 3), "text": sentence}]
    mid = len(sentence) // 2
    breaks = [m.end() for m in re.finditer(r"[，、；：]|——", sentence)]
    cut = min(breaks, key=lambda b: abs(b - mid)) if breaks else mid
    head, tail = sentence[:cut].strip(), sentence[cut:].strip()
    if not head or not tail:
        return [{"start": round(start, 3), "dur": round(dur, 3), "text": sentence}]
    share = dur * len(head) / len(sentence)
    return cues(head, start, share) + cues(tail, start + share, dur - share)


def speak(text: str, dest: Path, voice: str, rate: int) -> float:
    text = PERCENT.sub(r"百分之\1", text)
    for a, b in SPOKEN:
        text = text.replace(a, b)
    # say defaults to 22 kHz; BEI16@44100 is the format its aiff writer accepts
    # and is worth having for a submission video.
    subprocess.run(["say", "-v", voice, "-r", str(rate),
                    "--data-format=BEI16@44100", "-o", str(dest), text], check=True)
    info = subprocess.run(["afinfo", str(dest)], capture_output=True, text=True).stdout
    m = re.search(r"estimated duration: ([\d.]+)", info)
    return float(m.group(1)) if m else 0.0


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Meijia")
    ap.add_argument("--rate", type=int, default=200, help="words per minute passed to say")
    args = ap.parse_args()

    secs = sections()
    if not secs:
        print("no timed sections found in the script", file=sys.stderr)
        return 1

    for old in HERE.glob("*.aiff"):
        old.unlink()

    print(f"voice={args.voice} rate={args.rate}\n")
    clips: list[dict] = []
    subs: list[dict] = []
    marks: list[dict] = []
    at = 0.0

    for si, (head, text) in enumerate(secs, 1):
        sec_start = at
        for qi, sentence in enumerate(sentences(text), 1):
            dest = HERE / f"{si:02d}_{qi:02d}.aiff"
            dur = speak(sentence, dest, args.voice, args.rate)
            clips.append({"file": dest.name, "start": round(at, 3), "dur": round(dur, 3)})
            subs.extend(cues(sentence, at, dur))
            at += dur
        marks.append({
            "name": head,
            "start": round(sec_start, 3),
            "end": round(at, 3),
            "budget": round(at - sec_start + GAP, 3),
        })
        print(f"{head[:26]:<28}{at - sec_start:>6.1f}s")
        if si < len(secs):
            at += GAP

    (HERE / "narration.json").write_text(json.dumps(
        {"sections": marks, "clips": clips, "cues": subs, "total": round(at, 3)},
        ensure_ascii=False, indent=1), encoding="utf-8")

    srt = []
    for i, c in enumerate(subs, 1):
        srt.append(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['start'] + c['dur'])}\n{c['text']}\n")
    (HERE / "subtitles.srt").write_text("\n".join(srt), encoding="utf-8")

    print(f"\n{len(clips)} 句旁白 / {len(subs)} 條字幕，成片 {at:.1f}s "
          f"（{int(at // 60)}:{int(at % 60):02d}）")
    longest = max(subs, key=lambda c: len(c["text"]))
    print(f"最長字幕 {len(longest['text'])} 字：{longest['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
