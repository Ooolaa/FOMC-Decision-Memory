import unittest
from types import SimpleNamespace
from unittest import mock

from decision_memory.ai_member_explanation import (
    AiExplanationError,
    build_explanation_input,
    classify_ai_error,
    generate_member_explanation,
)


class AiMemberExplanationTests(unittest.TestCase):
    def setUp(self):
        self.forecast = {
            "meeting_id": "FOMC-2026-09-15",
            "forecast_as_of": "2026-08-27",
            "policy_prediction": {
                "action_class": "HOLD",
                "probabilities": {"CUT": 0.1, "HOLD": 0.8, "HIKE": 0.1},
            },
        }
        self.member = {
            "display_name": "Example Member",
            "predicted_vote": "FOR",
            "prior_vote_count": 4,
            "prior_dissent_count": 0,
            "prior_dissent_rate": 0.0,
            "vote_history": [],
            "inferred_concerns": [],
            "important_communications": [
                {
                    "document_id": "doc-1",
                    "publication_date": "2026-08-01",
                    "title": "Economic Outlook",
                    "excerpt": "Inflation is moderating.",
                    "source_url": "https://www.federalreserve.gov/example",
                }
            ],
        }

    def test_api_input_excludes_source_urls_and_full_document_text(self):
        payload = build_explanation_input(self.forecast, self.member)

        communication = payload["member"]["important_communications"][0]
        self.assertNotIn("source_url", communication)
        self.assertNotIn("text", communication)
        self.assertTrue(payload["guardrails"]["may_not_change_the_model_prediction"])

    def test_api_call_uses_user_scope_key_and_does_not_store_response(self):
        response = SimpleNamespace(status="completed", output_text="稽核說明")
        client = mock.Mock()
        client.responses.create.return_value = response
        with mock.patch(
            "decision_memory.ai_member_explanation.load_user_scope_openai_key",
            return_value="fake-user-key",
        ), mock.patch("openai.OpenAI", return_value=client) as constructor:
            result = generate_member_explanation(
                self.forecast,
                self.member,
                model_id="gpt-5.6-terra",
            )

        self.assertEqual(result, "稽核說明")
        constructor.assert_called_once_with(
            api_key="fake-user-key",
            timeout=90.0,
            max_retries=2,
        )
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertFalse(request["store"])
        self.assertEqual(request["max_output_tokens"], 4000)
        self.assertEqual(request["reasoning"], {"effort": "low"})

    def test_output_limit_incomplete_has_a_specific_retriable_error(self):
        response = SimpleNamespace(
            status="incomplete",
            output_text="",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )
        client = mock.Mock()
        client.responses.create.return_value = response
        with mock.patch(
            "decision_memory.ai_member_explanation.load_user_scope_openai_key",
            return_value="fake-user-key",
        ), mock.patch("openai.OpenAI", return_value=client):
            with self.assertRaises(AiExplanationError) as caught:
                generate_member_explanation(
                    self.forecast,
                    self.member,
                    model_id="gpt-5.6-terra",
                )

        self.assertEqual(caught.exception.code, "AI_OUTPUT_LIMIT")
        self.assertTrue(caught.exception.retriable)

    def test_quota_failure_has_a_specific_safe_user_message(self):
        error = RuntimeError("raw provider detail")
        error.status_code = 429
        error.code = "insufficient_quota"

        classified = classify_ai_error(error)

        self.assertIsInstance(classified, AiExplanationError)
        self.assertEqual(classified.code, "AI_QUOTA")
        self.assertIn("額度", classified.user_message)
        self.assertNotIn("raw provider detail", classified.user_message)


if __name__ == "__main__":
    unittest.main()
