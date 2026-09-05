from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


MODEL_LIST_URL = "https://api.openai.com/v1/models"
DEFAULT_SPEC_PATH = (
    Path(__file__).resolve().parents[1]
    / "model_spec"
    / "gpt-5.6-terra-standard-2026-08-27.json"
)
EXPECTED_MODEL_ID = "gpt-5.6-terra"
APPROVED_STANDARD_PRICE_CARDS = {
    "gpt-5.6-terra": {
        "standard": {
            "input": 2.0,
            "cached_input": 0.2,
            "cache_write": 2.5,
            "output": 12.0,
        },
        "long_context": {
            "input": 4.0,
            "cached_input": 0.4,
            "cache_write": 5.0,
            "output": 18.0,
        },
    },
    "gpt-5.6-luna": {
        "standard": {
            "input": 0.2,
            "cached_input": 0.02,
            "cache_write": 0.25,
            "output": 1.2,
        },
        "long_context": {
            "input": 0.4,
            "cached_input": 0.04,
            "cache_write": 0.5,
            "output": 1.8,
        },
    },
}


def load_model_spec(spec_path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    with spec_path.open("r", encoding="utf-8") as source:
        spec = json.load(source)
    model_id = spec.get("model_id")
    price_card = APPROVED_STANDARD_PRICE_CARDS.get(model_id)
    if price_card is None:
        raise ValueError(
            "Model spec must pin one of the approved comparison models: "
            + ", ".join(sorted(APPROVED_STANDARD_PRICE_CARDS))
        )
    if spec.get("service_tier") != "standard":
        raise ValueError("Model spec must pin the standard service tier")
    if spec.get("pricing_usd_per_million") != price_card["standard"]:
        raise ValueError("Model spec prices do not match the frozen standard rate card")
    if spec.get("long_context_pricing_usd_per_million") != price_card["long_context"]:
        raise ValueError(
            "Model spec prices do not match the frozen long-context rate card"
        )
    for key in ("model_source_url", "pricing_source_url"):
        if not str(spec.get(key, "")).startswith("https://developers.openai.com/"):
            raise ValueError(f"{key} must use official OpenAI documentation")
    return spec


def _credential_fingerprint(value: str | None) -> dict[str, Any]:
    if not value:
        return {"present": False, "length": 0, "sha256_prefix": None}
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {
        "present": True,
        "length": len(value),
        "sha256_prefix": digest[:10],
    }


def load_user_scope_openai_key() -> str:
    try:
        import winreg
    except ImportError as error:  # pragma: no cover - Windows project guard
        raise RuntimeError("Windows User-scope credential lookup is unavailable") from error
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "OPENAI_API_KEY")
    except FileNotFoundError as error:
        raise RuntimeError("User-scope OPENAI_API_KEY is missing") from error
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("User-scope OPENAI_API_KEY is empty")
    return value.strip()


def credential_report(user_key: str) -> dict[str, Any]:
    process_value = os.environ.get("OPENAI_API_KEY")
    user = _credential_fingerprint(user_key)
    process = _credential_fingerprint(process_value)
    return {
        "approved_source": "Windows User scope",
        "user": user,
        "process": process,
        "process_matches_user": bool(
            user["present"]
            and process["present"]
            and user["length"] == process["length"]
            and user["sha256_prefix"] == process["sha256_prefix"]
        ),
    }


def fetch_account_model_ids(
    api_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> set[str]:
    request = urllib.request.Request(
        MODEL_LIST_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with opener(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("OpenAI model-list response has no data array")
    return {
        item["id"]
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def audit_account_models(
    model_ids: Iterable[str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    available = set(model_ids)
    expected = str(spec["model_id"])
    exact_available = expected in available
    return {
        "model_id": expected,
        "selected_model": expected if exact_available else None,
        "exact_model_available": exact_available,
        "account_model_count": len(available),
        "service_tier": spec["service_tier"],
        "official_price_checked_at": spec["checked_at"],
        "official_pricing_usd_per_million": spec["pricing_usd_per_million"],
        "cost_status": spec.get(
            "cost_status",
            "OFFICIAL_UNIT_PRICES_VERIFIED_USAGE_ENVELOPE_UNVERIFIED",
        ),
        "model_source_url": spec["model_source_url"],
        "pricing_source_url": spec["pricing_source_url"],
    }


def assert_model_ready(report: dict[str, Any]) -> None:
    if not report.get("exact_model_available"):
        raise RuntimeError(
            f"Required model {report.get('model_id', EXPECTED_MODEL_ID)} "
            "is not visible to this API account; no substitute was selected"
        )


def run_model_preflight(spec_path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    spec = load_model_spec(spec_path)
    user_key = load_user_scope_openai_key()
    report = audit_account_models(fetch_account_model_ids(user_key), spec)
    report["credential"] = credential_report(user_key)
    assert_model_ready(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the exact R5 OpenAI model without paid inference."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    args = parser.parse_args()
    report = run_model_preflight(args.spec)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
