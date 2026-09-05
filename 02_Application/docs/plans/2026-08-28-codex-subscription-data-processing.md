# Codex subscription data-processing execution plan

Execution status: M1–M4 completed on 2026-08-28; M5 remains pending.

## Goal

Process the remaining development-only FOMC artifacts with ChatGPT-managed Codex
subscription access, while making it mechanically impossible for this path to
fall back to the OpenAI Platform API. Production remains on the existing
Responses API runner.

## Verified assumptions

- `codex-cli 0.147.0` is installed.
- `codex login status` reports `Logged in using ChatGPT`.
- `CODEX_API_KEY` is absent at Process, User, and Machine scope.
- `OPENAI_API_KEY` is present at Process/User scope, so the subscription child
  process must remove it explicitly.
- The workspace is not a Git repository, so bounded Codex jobs require
  `--skip-git-repo-check`.
- The Frozen cohort is the existing 45-case `per_case` list in
  `artifacts/evaluation/frozen_45_policy_baselines_v1.json`.
- The current case builder fails for the first Frozen case because its generic
  latest-document query selects transcript PDFs but sends every file to the HTML
  parser. This must be fixed before any model job is started.

## Dependency map

```text
Frozen-45 manifest
  -> cutoff-safe case bundle builder
  -> runtime JSON Schema with evidence-id enum
  -> Codex subscription executor (ChatGPT auth only)
  -> schema + semantic validation
  -> append-only per-case artifacts
  -> deterministic Frozen-45 evaluator/report

Windows auth state
  -> fail-closed auth preflight
  -> child environment without OPENAI_API_KEY/CODEX_API_KEY
  -> codex exec --output-schema
```

## Milestones

### M1 - Input and zero-API boundary

- Restrict the five-document simulation bundle to the HTML statement/minutes
  evidence surface already used by the historical simulator; transcript PDFs
  remain in the persona corpus and are not silently decoded as HTML.
- Add a regression test for the 2021-01-26 Frozen case.
- Add a ChatGPT-auth preflight that fails unless the cleaned child process still
  reports `Logged in using ChatGPT`.

Verification: all 45 case bundles build locally; no Codex/model request is made.

Rollback: revert the query and subscription module; source databases are never
mutated.

### M2 - Subscription stage executor

- Run each existing stage through `codex exec` with the runtime schema,
  stage-specific reasoning effort, read-only sandbox, ephemeral session, and
  cleaned environment.
- Reuse the existing semantic validator and allow one semantic repair only.
- Label every result `codex_subscription`; record Codex token usage but set
  Platform API cost to zero rather than estimating an API bill.

Verification: mocked process tests cover auth failure, environment cleaning,
schema failure, semantic repair, and successful five-stage assembly.

Rollback: the existing API runner remains unchanged and is not invoked.

### M3 - One-case live subscription smoke

- Execute one representative case with ChatGPT-managed auth.
- Validate output schema, evidence IDs, roster coverage, Chair proposal, and vote
  balance.
- Stop on subscription rate limit or authentication drift; never switch to API.

Verification: one append-only artifact plus command transcript showing ChatGPT
auth and zero Platform API path.

### M4 - Resumable Frozen-45 processing

- Process cases sequentially and write one immutable artifact per case.
- Skip only byte-identical completed artifacts; fail on conflicting output.
- Produce a batch manifest with completed/failed/pending counts and hashes.

Verification: 45/45 terminal case states and deterministic aggregate metrics.

Rollback: remove only the newly generated subscription artifact directory after
verifying its resolved path; no database rollback is needed.

### M5 - Batch DecisionTrace extraction

- Reuse the same subscription/auth boundary for the preregistered training
  document corpus.
- Keep machine extraction separate from the human-audited golden fixture and
  label audit status explicitly.

Verification: schema-valid traces, cutoff-safe evidence IDs, sampled human audit,
and a corpus manifest. This milestone starts only after M4 proves the executor.

## M1-M4 completion evidence

- Frozen preflight: 45/45 bundles; every case has five cutoff-safe HTML
  statement/minutes documents, 6,816 economic observations and 9–12
  participants.
- Authentication boundary: Codex reported `Logged in using ChatGPT`; child
  processes removed `OPENAI_API_KEY`, `CODEX_API_KEY` and `OPENAI_BASE_URL`.
- Batch terminal state: `COMPLETED`, 45/45 cases, 225 stage requests, zero
  semantic repair, zero Platform API calls and US$0 Platform API cost.
- Policy direction: 45/45. Dissent: TP=7, FP=14, FN=16, TN=480,
  precision=0.333, recall=0.304 and F1=0.318.
- Recorded subscription workload: 69,865,767 input tokens, 2,163,200 cached
  input tokens, 133,565 output tokens and 13,761 reasoning output tokens. These
  are not an API bill.
- Verification: 123 unit tests pass; both SQLite databases return
  `integrity_check=ok` and no foreign-key rows.
- Authoritative status artifact:
  `artifacts/codex_subscription/frozen45_v1/batch_status.json`.

M4 therefore proves the resumable subscription data-processing path, but not a
production/API promotion gate. M5 is deliberately not inferred from M4.

## Risks

- Subscription limits may stop the batch. The resumable manifest records the
  pending suffix and waits; it never falls back to the API key.
- Codex-agent execution is not an API-equivalent Terra benchmark. Subscription
  artifacts are development data and cannot be presented as production API
  evaluation.
- Large case prefixes may exceed Codex context limits. The runner fails closed;
  evidence is compacted only through a separately reviewed, hash-versioned rule.

## Out of scope

- Paid Responses API calls during development.
- Replacing the production API runner.
- Claiming subscription artifacts are statistically equivalent to the frozen
  production-model benchmark.
- Fine-tuning, reinforcement learning, or Self-Harness promotion.
