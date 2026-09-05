import io
import json
import unittest
from pathlib import Path

from decision_memory.model_preflight import (
    assert_model_ready,
    audit_account_models,
    fetch_account_model_ids,
    load_model_spec,
)


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class ModelPreflightTests(unittest.TestCase):
    def test_model_spec_freezes_exact_model_and_standard_prices(self):
        spec = load_model_spec(
            Path("model_spec/gpt-5.6-terra-standard-2026-08-27.json")
        )

        self.assertEqual(spec["model_id"], "gpt-5.6-terra")
        self.assertEqual(spec["service_tier"], "standard")
        self.assertEqual(spec["pricing_usd_per_million"]["input"], 2.0)
        self.assertEqual(spec["pricing_usd_per_million"]["cached_input"], 0.2)
        self.assertEqual(spec["pricing_usd_per_million"]["cache_write"], 2.5)
        self.assertEqual(spec["pricing_usd_per_million"]["output"], 12.0)

    def test_luna_comparison_spec_freezes_exact_model_and_standard_prices(self):
        spec = load_model_spec(
            Path("model_spec/gpt-5.6-luna-standard-2026-08-28.json")
        )

        self.assertEqual(spec["model_id"], "gpt-5.6-luna")
        self.assertEqual(spec["service_tier"], "standard")
        self.assertEqual(
            spec["pricing_usd_per_million"],
            {
                "input": 0.2,
                "cached_input": 0.02,
                "cache_write": 0.25,
                "output": 1.2,
            },
        )
        self.assertEqual(
            spec["long_context_pricing_usd_per_million"],
            {
                "input": 0.4,
                "cached_input": 0.04,
                "cache_write": 0.5,
                "output": 1.8,
            },
        )

    def test_unapproved_model_spec_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "approved comparison models"):
            load_model_spec(Path("tests/fixtures/unapproved-model-spec.json"))

    def test_fetch_account_models_uses_bearer_key_without_exposing_it(self):
        captured = {}

        def opener(request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _FakeResponse(
                {"data": [{"id": "gpt-5.6-luna"}, {"id": "gpt-5.6-terra"}]}
            )

        model_ids = fetch_account_model_ids("secret-test-key", opener=opener)

        self.assertEqual(model_ids, {"gpt-5.6-luna", "gpt-5.6-terra"})
        self.assertEqual(captured["authorization"], "Bearer secret-test-key")
        self.assertEqual(captured["url"], "https://api.openai.com/v1/models")
        self.assertEqual(captured["timeout"], 30)

    def test_missing_exact_model_fails_closed_without_substitution(self):
        spec = {
            "model_id": "gpt-5.6-terra",
            "service_tier": "standard",
            "checked_at": "2026-08-27",
            "model_source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
            "pricing_source_url": "https://developers.openai.com/api/docs/pricing",
            "pricing_usd_per_million": {
                "input": 2.0,
                "cached_input": 0.2,
                "cache_write": 2.5,
                "output": 12.0,
            },
        }
        report = audit_account_models({"gpt-5.6-sol"}, spec)

        self.assertFalse(report["exact_model_available"])
        self.assertIsNone(report["selected_model"])
        with self.assertRaisesRegex(RuntimeError, "gpt-5.6-terra"):
            assert_model_ready(report)


if __name__ == "__main__":
    unittest.main()
