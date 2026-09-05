from __future__ import annotations

import json
import os
from typing import Any

from decision_memory.model_preflight import load_user_scope_openai_key


DEFAULT_MODEL = "gpt-5.6-terra"


class AiExplanationError(RuntimeError):
    def __init__(self, code: str, user_message: str, *, retriable: bool) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retriable = retriable


def classify_ai_error(error: Exception) -> AiExplanationError:
    status_code = getattr(error, "status_code", None)
    provider_code = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        provider_code = provider_code or body.get("code")
        nested = body.get("error")
        if isinstance(nested, dict):
            provider_code = provider_code or nested.get("code")
    error_name = type(error).__name__.casefold()
    if status_code == 401 or "authentication" in error_name:
        return AiExplanationError(
            "AI_AUTH",
            "AI 金鑰未通過驗證；請確認 Windows 使用者層級的 OPENAI_API_KEY。",
            retriable=False,
        )
    if status_code == 403 or "permission" in error_name:
        return AiExplanationError(
            "AI_MODEL_ACCESS",
            "目前 API 帳戶無法使用指定模型；基礎預測與證據仍可查看。",
            retriable=False,
        )
    if provider_code == "insufficient_quota":
        return AiExplanationError(
            "AI_QUOTA",
            "OpenAI API 額度不足；補充額度後可重試，基礎預測不受影響。",
            retriable=False,
        )
    if status_code == 429 or "ratelimit" in error_name:
        return AiExplanationError(
            "AI_RATE_LIMIT",
            "OpenAI API 暫時達到速率上限；請稍後重試。",
            retriable=True,
        )
    if status_code == 400 or "badrequest" in error_name:
        return AiExplanationError(
            "AI_REQUEST",
            "AI 請求格式未通過服務端驗證；請交由工程端檢查。",
            retriable=False,
        )
    if (
        "connection" in error_name
        or "timeout" in error_name
        or isinstance(error, TimeoutError)
    ):
        return AiExplanationError(
            "AI_NETWORK",
            "無法連線至 OpenAI API 或請求逾時；請確認網路後重試。",
            retriable=True,
        )
    return AiExplanationError(
        "AI_UNKNOWN",
        "AI 統整未完成；系統已保留診斷代碼，基礎預測與證據不受影響。",
        retriable=False,
    )


def user_scope_key_available() -> bool:
    try:
        load_user_scope_openai_key()
    except RuntimeError:
        return False
    return True


def build_explanation_input(forecast: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
    return {
        "meeting": {
            "meeting_id": forecast["meeting_id"],
            "forecast_as_of": forecast["forecast_as_of"],
            "predicted_policy_action": forecast["policy_prediction"]["action_class"],
            "reference_probabilities": forecast["policy_prediction"]["probabilities"],
        },
        "member": {
            "display_name": member["display_name"],
            "predicted_vote": member["predicted_vote"],
            "prior_vote_count": member["prior_vote_count"],
            "prior_dissent_count": member["prior_dissent_count"],
            "prior_dissent_rate": member["prior_dissent_rate"],
            "vote_history": member["vote_history"],
            "inferred_concerns": member["inferred_concerns"],
            "important_communications": [
                {
                    "document_id": item["document_id"],
                    "publication_date": item["publication_date"],
                    "title": item["title"],
                    "excerpt": item["excerpt"],
                }
                for item in member["important_communications"]
            ],
        },
        "guardrails": {
            "historical_records_are_evidence": True,
            "concerns_are_deterministic_inferences_not_facts": True,
            "may_not_change_the_model_prediction": True,
            "may_not_invent_evidence": True,
        },
    }


def generate_member_explanation(
    forecast: dict[str, Any],
    member: dict[str, Any],
    *,
    model_id: str | None = None,
) -> str:
    from openai import OpenAI

    selected_model = model_id or os.environ.get("FOMC_AI_EXPLAIN_MODEL", DEFAULT_MODEL)
    payload = build_explanation_input(forecast, member)
    client = OpenAI(
        api_key=load_user_scope_openai_key(),
        timeout=90.0,
        max_retries=2,
    )
    try:
        response = client.responses.create(
            model=selected_model,
            service_tier="default",
            store=False,
            max_output_tokens=4000,
            reasoning={"effort": "low"},
            input=[
                {
                    "role": "developer",
                    "content": (
                        "你是聯準會預測結果的稽核說明器。請用繁體中文，分成『結論』、"
                        "『可驗證證據』、『推定而非事實』、『限制』四小段。只能使用輸入中的"
                        "資料，不得改變預測、不得替委員編造立場，也不得把規則式關注議題稱為事實。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            truncation="disabled",
        )
    except Exception as error:
        raise classify_ai_error(error) from error
    incomplete_details = getattr(response, "incomplete_details", None)
    incomplete_reason = (
        incomplete_details.get("reason")
        if isinstance(incomplete_details, dict)
        else getattr(incomplete_details, "reason", None)
    )
    if response.status != "completed" or not response.output_text:
        if incomplete_reason == "max_output_tokens":
            raise AiExplanationError(
                "AI_OUTPUT_LIMIT",
                "AI 回應超出輸出上限；請重試，基礎預測與證據不受影響。",
                retriable=True,
            )
        raise AiExplanationError(
            "AI_INCOMPLETE",
            "AI 回應未完整產生；請重試，基礎預測與證據不受影響。",
            retriable=True,
        )
    return str(response.output_text)
