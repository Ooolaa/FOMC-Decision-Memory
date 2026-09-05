from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from decision_memory.llm_sample import (
    STAGES,
    STAGE_REASONING_EFFORT,
    _runtime_stage_schema,
    _stage_instruction,
    build_case_bundle,
    render_stable_prefix,
    semantic_violations,
)
from decision_memory.model_preflight import DEFAULT_SPEC_PATH, load_model_spec
from decision_memory.offline_simulator import validate_simulation_output


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_API_ENVIRONMENT_NAMES = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_BASE_URL",
)
SUBSCRIPTION_CONFIRMATION = "RUN_CODEX_SUBSCRIPTION_DATA_PROCESSING"


class CodexSubscriptionError(RuntimeError):
    pass


def cleaned_subscription_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    for name in BLOCKED_API_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    environment["NO_COLOR"] = "1"
    return environment


def _completed_process_runner(command: list[str], **kwargs: Any) -> Any:
    return subprocess.run(command, **kwargs)


def _jsonl_events(value: str) -> list[dict[str, Any]]:
    events = []
    for line in value.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CodexSubscriptionError("Codex emitted non-JSON stdout") from error
        if not isinstance(event, dict):
            raise CodexSubscriptionError("Codex JSONL event must be an object")
        events.append(event)
    return events


class CodexSubscriptionExecutor:
    def __init__(
        self,
        *,
        codex_command: str = "codex",
        command_runner: Callable[..., Any] = _completed_process_runner,
        temporary_root: Path | None = None,
        base_environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.codex_command = codex_command
        self.command_runner = command_runner
        self.temporary_root = (
            temporary_root
            if temporary_root is not None
            else ROOT / "artifacts" / "codex_subscription" / ".tmp"
        )
        self.base_environment = dict(
            os.environ if base_environment is None else base_environment
        )
        self.timeout_seconds = timeout_seconds

    def _environment(self) -> dict[str, str]:
        return cleaned_subscription_environment(self.base_environment)

    def verify_authentication(self) -> dict[str, Any]:
        result = self.command_runner(
            [self.codex_command, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30.0,
            env=self._environment(),
            cwd=ROOT,
        )
        status_text = "\n".join(
            part for part in (str(result.stdout or ""), str(result.stderr or "")) if part
        )
        if result.returncode != 0 or "Logged in using ChatGPT" not in status_text:
            raise CodexSubscriptionError(
                "Codex must use ChatGPT subscription authentication; API-key or "
                "unknown authentication is rejected"
            )
        return {
            "status": "CHATGPT_SUBSCRIPTION_AUTHENTICATED",
            "api_environment_removed": list(BLOCKED_API_ENVIRONMENT_NAMES),
        }

    def run_stage(
        self,
        *,
        model_id: str,
        reasoning_effort: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.verify_authentication()
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="codex_subscription_", dir=self.temporary_root
        ) as directory:
            directory_path = Path(directory)
            schema_path = directory_path / "schema.json"
            output_path = directory_path / "output.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            command = [
                self.codex_command,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--json",
                "--color",
                "never",
                "-C",
                str(ROOT),
                "-m",
                model_id,
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ]
            started = time.perf_counter()
            result = self.command_runner(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=self._environment(),
                cwd=ROOT,
            )
            latency = time.perf_counter() - started
            if result.returncode != 0:
                stdout_tail = str(result.stdout or "")[-4_000:]
                stderr_tail = str(result.stderr or "")[-2_000:]
                raise CodexSubscriptionError(
                    f"Codex subscription stage failed with exit "
                    f"{result.returncode}: STDOUT={stdout_tail} STDERR={stderr_tail}"
                )
            if not output_path.exists():
                raise CodexSubscriptionError("Codex produced no structured output file")
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise CodexSubscriptionError(
                    "Codex structured output is not valid JSON"
                ) from error
            events = _jsonl_events(str(result.stdout or ""))
            disallowed_item_types = {
                "command_execution",
                "file_change",
                "mcp_tool_call",
                "web_search",
            }
            observed_tool_types = sorted(
                {
                    str((event.get("item") or {}).get("type"))
                    for event in events
                    if event.get("type") in {"item.started", "item.completed"}
                    and str((event.get("item") or {}).get("type"))
                    in disallowed_item_types
                }
            )
            if observed_tool_types:
                raise CodexSubscriptionError(
                    "Codex data-processing stage attempted forbidden tool use: "
                    + ", ".join(observed_tool_types)
                )
            completed = [event for event in events if event.get("type") == "turn.completed"]
            if len(completed) != 1:
                raise CodexSubscriptionError(
                    "Codex stage requires exactly one turn.completed event"
                )
            thread_events = [
                event for event in events if event.get("type") == "thread.started"
            ]
            usage = completed[0].get("usage") or {}
            return {
                "output": output,
                "usage": {
                    "input_tokens": int(usage.get("input_tokens", 0) or 0),
                    "cached_input_tokens": int(
                        usage.get("cached_input_tokens", 0) or 0
                    ),
                    "output_tokens": int(usage.get("output_tokens", 0) or 0),
                    "reasoning_output_tokens": int(
                        usage.get("reasoning_output_tokens", 0) or 0
                    ),
                },
                "latency_seconds": round(latency, 6),
                "thread_id": (
                    thread_events[0].get("thread_id") if thread_events else None
                ),
            }


def run_subscription_sample(
    executor: CodexSubscriptionExecutor,
    bundle: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    schema, allowed_evidence_ids = _runtime_stage_schema(bundle)
    validator = Draft202012Validator(schema)
    stable = render_stable_prefix(bundle)
    prior: dict[str, Any] = {}
    usage_records = []

    for stage in STAGES:
        dynamic = _stage_instruction(stage, prior)
        for attempt in (1, 2):
            prompt = (
                stable
                + "\n\n"
                + dynamic
                + "\n\nDo not call tools or inspect the filesystem. Return only the "
                "JSON object required by the supplied schema."
            )
            result = executor.run_stage(
                model_id=spec["model_id"],
                reasoning_effort=STAGE_REASONING_EFFORT[stage],
                prompt=prompt,
                schema=schema,
            )
            envelope = result["output"]
            schema_errors = sorted(
                validator.iter_errors(envelope), key=lambda item: list(item.path)
            )
            if schema_errors:
                raise CodexSubscriptionError(
                    f"Schema-layer validation failure for {stage}: "
                    f"{schema_errors[0].message}"
                )
            usage_records.append(
                {
                    "stage": stage,
                    "attempt": attempt,
                    "thread_id": result.get("thread_id"),
                    "reasoning_effort": STAGE_REASONING_EFFORT[stage],
                    "latency_seconds": result["latency_seconds"],
                    **result["usage"],
                }
            )
            violations = semantic_violations(stage, envelope, bundle, prior)
            if not violations:
                prior[stage] = envelope
                break
            if attempt == 2:
                raise CodexSubscriptionError(
                    f"Semantic repair failed for {stage}: {'; '.join(violations)}"
                )
            dynamic = (
                _stage_instruction(stage, prior)
                + " REPAIR_THESE_SEMANTIC_VIOLATIONS="
                + json.dumps(violations, ensure_ascii=False)
                + " ALLOWED_EVIDENCE_IDS="
                + json.dumps(
                    allowed_evidence_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + " INVALID_OUTPUT="
                + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            )

    final = {
        "schema_version": "simulation_output_v1",
        "meeting_id": bundle["meeting_id"],
        "synthetic": True,
        "profiles": prior["profiles"]["profiles"],
        "discussion": [
            {
                "participant_id": item["participant_id"],
                "synthetic_text": item["synthetic_text"],
            }
            for item in prior["openings"]["openings"]
        ],
        "final_proposal": prior["chair"]["final_proposal"],
        "votes": [
            {"participant_id": item["participant_id"], "choice": item["choice"]}
            for item in prior["votes"]["votes"]
        ],
    }
    semantic_report = validate_simulation_output(
        final,
        expected_meeting_id=str(bundle["meeting_id"]),
    )
    totals = {
        name: sum(int(record[name]) for record in usage_records)
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }
    return {
        "status": "SUBSCRIPTION_SAMPLE_COMPLETED",
        "execution_provider": "codex_subscription",
        "billing_route": "chatgpt_subscription",
        "platform_api_cost_usd": 0.0,
        "model_id": spec["model_id"],
        "meeting_id": bundle["meeting_id"],
        "bundle_hash": bundle["bundle_hash"],
        "stage_order": list(STAGES),
        "case_stage_affinity": "SEQUENTIAL_NO_INTERLEAVING",
        "semantic_validation": semantic_report,
        "usage": usage_records,
        "usage_totals": totals,
        "output": final,
    }


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.resolve()
    if resolved.exists():
        existing = json.loads(resolved.read_text(encoding="utf-8"))
        if existing == payload:
            return
        raise FileExistsError(f"Refusing to overwrite different artifact: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the five-stage simulator through ChatGPT-managed Codex auth."
    )
    parser.add_argument("--source", type=Path, default=Path("fred_fomc_real.sqlite"))
    parser.add_argument("--app", type=Path, default=Path("fomc_simulation.sqlite"))
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--document-count", type=int, default=5)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--bundle-output", type=Path, required=True)
    parser.add_argument("--run-output", type=Path, required=True)
    parser.add_argument("--confirmation", required=True)
    args = parser.parse_args()
    if args.confirmation != SUBSCRIPTION_CONFIRMATION:
        raise CodexSubscriptionError(
            f"Subscription processing requires --confirmation "
            f"{SUBSCRIPTION_CONFIRMATION}"
        )
    spec = load_model_spec(args.spec)
    bundle = build_case_bundle(
        args.source,
        args.app,
        meeting_id=args.meeting_id,
        document_count=args.document_count,
    )
    _write_new_json(args.bundle_output, bundle)
    executor = CodexSubscriptionExecutor()
    executor.verify_authentication()
    report = run_subscription_sample(executor, bundle, spec)
    _write_new_json(args.run_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
