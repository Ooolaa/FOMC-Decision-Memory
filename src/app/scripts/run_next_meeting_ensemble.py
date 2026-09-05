from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision_memory.forecast_ensemble import (  # noqa: E402
    FORWARD_CONFIRMATION,
    run_forward_subscription_ensemble,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args()
    result = run_forward_subscription_ensemble(
        source_database=ROOT / "fred_fomc_real.sqlite",
        app_database=ROOT / "fomc_simulation.sqlite",
        reaction_artifact_path=(
            ROOT / "artifacts/reaction/pooled_ordered_logit_v1.json"
        ),
        policy_evaluation_path=(
            ROOT / "artifacts/evaluation/frozen_45_policy_baselines_v1.json"
        ),
        vote_evaluation_path=(
            ROOT / "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json"
        ),
        variant_matrix_path=(
            ROOT / "artifacts/evaluation/r5_subscription_variant_matrix_v1.json"
        ),
        ablation_spec_path=(
            ROOT / "evaluation_spec/hackathon_r5_variants_v1.json"
        ),
        model_spec_path=(
            ROOT / "model_spec/gpt-5.6-terra-standard-2026-08-27.json"
        ),
        official_context_path=(
            ROOT / "fixtures/next_meeting_official_context_2026-09-01.json"
        ),
        communications_database=(
            ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
        ),
        output_directory=(
            ROOT / "artifacts/forecast/fomc_2026_09_15_ensemble_v1"
        ),
        confirmation=args.confirmation,
    )
    print(
        json.dumps(
            {
                "meeting_id": result["meeting_id"],
                "forecast_as_of": result["forecast_as_of"],
                "locked_at": result["locked_at"],
                "policy": result["combined"]["policy"],
                "model_count": len(result["model_rows"]),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    if FORWARD_CONFIRMATION != "RUN_NEXT_MEETING_SUBSCRIPTION_ENSEMBLE":
        raise RuntimeError("Unexpected forward confirmation contract")
    raise SystemExit(main())
