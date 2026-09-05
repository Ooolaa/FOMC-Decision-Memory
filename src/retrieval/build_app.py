#!/usr/bin/env python3
"""Assemble FOMC_RAG_Vote_Simulator.html from the template + built index.

Injects three payloads into app_template.html:
  __INDEX_JSON__   the RAG index built by build_rag_index.py
  __MODEL_JSON__   the frozen R5 pooled ordered-logit coefficients
  __ROSTER_JSON__  selectable committee rosters

Nothing under src/app/ is read for anything but its JSON artifacts, and
nothing there is written.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
APP = ROOT / "src" / "app"
INDEX_PATH = HERE / "fomc_rag_index.json"
TEMPLATE = HERE / "app_template.html"
OUT = ROOT / "dist" / "FOMC_RAG_Vote_Simulator.html"

LOGIT = APP / "artifacts/reaction/pooled_ordered_logit_v1.json"
ENSEMBLE = APP / "artifacts/forecast/fomc_2026_09_15_ensemble_v1/runs/named_persona_reaction.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_rosters(index: dict) -> dict:
    """Only the R5 target-meeting roster: the UI no longer offers a choice."""
    rosters: dict[str, dict] = {}

    if ENSEMBLE.exists():
        profiles = load(ENSEMBLE)["output"]["profiles"]
        rosters["r5_2026"] = {
            "label": "R5 目標會議名單 · FOMC-2026-09-15（12 人）",
            "ids": [p["participant_id"] for p in profiles],
            "names": {p["participant_id"]: p["display_name"] for p in profiles},
        }

    return rosters


def main() -> int:
    if not INDEX_PATH.exists():
        print("missing index; run build_rag_index.py first", file=sys.stderr)
        return 1
    index = load(INDEX_PATH)
    logit = load(LOGIT)

    index["meta"]["dissent_events"] = sum(m["dissents"] for m in index["members"])
    index["meta"].pop("unknown_action", None)

    model = {
        "means": logit["means"],
        "scales": logit["scales"],
        "coef": logit["coefficients"],
        "cuts": logit["cutpoints"],
        "model_id": logit["model_id"],
        "training": f"{logit['training_start']}..{logit['training_end']}",
        "accuracy": logit["accuracy"],
    }

    html = TEMPLATE.read_text(encoding="utf-8")
    for token, payload in (
        ("__INDEX_JSON__", index),
        ("__MODEL_JSON__", model),
        ("__ROSTER_JSON__", build_rosters(index)),
    ):
        if token not in html:
            print(f"template is missing {token}", file=sys.stderr)
            return 1
        html = html.replace(token, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1e6:.2f} MB)")
    print(f"  meetings={index['meta']['meetings']} chunks={index['meta']['chunks']} "
          f"members={index['meta']['members']} dissents={index['meta']['dissent_events']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
