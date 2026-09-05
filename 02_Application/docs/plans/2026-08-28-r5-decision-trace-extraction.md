# R5 DecisionTrace subscription extraction plan

## Goal

Complete the R5 pre-hackathon DecisionTrace batch extraction for the fixed
50-meeting 2006-2020 transcript corpus using ChatGPT-managed Codex
authentication, with no OpenAI Platform API fallback. Preserve the existing
human-audited golden fixture as a separate evidence class.

## Verified assumptions

- `fomc_simulation.sqlite` contains exactly 50 transcript meetings and 11,876
  segments; 9,269 segments resolve to a roster participant.
- All 50 meetings have a statement, outcome, vote labels and roster.
- 49 meetings have minutes. `FOMC-2020-03-02` is the sole official sparse
  exception and has no independent minutes.
- Transcript meetings contain 33-445 segments (mean 237.5), so one bounded
  extraction request per meeting is feasible and independently resumable.
- The existing `decision_trace_v1` schema and deterministic outcome/vote
  validator are the authoritative output contract.
- The formal app DB and human-audited fixture must not be overwritten by
  unaudited machine extraction.

## Dependency map

```text
50-meeting transcript manifest
  -> statement/minutes + transcript-segment bundle
  -> dynamic evidence/participant/series JSON Schema
  -> ChatGPT-subscription Codex extraction
  -> schema + excerpt + roster + outcome + vote validation
  -> append-only per-meeting JSON artifacts
  -> deterministic audit queue and batch report
  -> reviewed import into a derived app DB
```

## Milestones

### M1 - Evidence boundary and bundle

- Extend DecisionTrace evidence validation only for same-meeting transcript
  documents represented by hash-verified `transcript_segment` rows.
- Keep statement/minutes as `label_only`; transcript evidence remains
  post-meeting extraction material and never becomes historical Case input.
- Build all 50 bundles and fail on missing documents, hashes, outcomes, votes,
  roster or monitor-series allowlist.

Verification: 50 deterministic bundles, exact sparse exception count of one,
zero source/app DB writes.

Rollback: revert code; generated artifacts are isolated below
`artifacts/codex_subscription/decision_trace_50_v4/`.

### M2 - Subscription extractor

- Generate a runtime schema with exact `document_id`, `participant_id`,
  meeting/outcome/vote constants and allowed economic series.
- Run one high-reasoning extraction request per meeting.
- Allow one repair only for concrete semantic violations; schema/refusal or
  incomplete output fails directly.
- Remove API credential/base-URL variables and require ChatGPT authentication.

Verification: mocked tests cover dynamic enums, transcript excerpts, invalid
cross-meeting evidence, outcome/vote mismatch, resume and fail-closed behavior.

Rollback: production Responses API runner remains unchanged.

### M3 - Five-meeting usage sample

- Run a chronological/episode-spanning five-meeting sample through the final
  extractor.
- Record tokens, latency, repair count and deterministic validation.
- Stop on subscription/authentication limits; do not switch to API.

Verification: five immutable artifacts and one sample report.

### M4 - Full 50-meeting extraction

- Resume sequentially to 50/50 terminal states.
- Record every bundle/run hash and aggregate structural metrics.
- Produce a deterministic audit queue covering early/middle/late meetings,
  action classes, dissents and the 2020 sparse-document exception.

Verification: 50/50 completed, zero hash mismatch, zero invalid excerpt,
outcome/vote validation pass for every case.

### M5 - Reviewed import

- Import only after the audit sample records reviewer/status fields.
- Write first to a named derived DB copy; run integrity/FK and UI regression.
- Preserve the existing golden fixture and identify machine traces by an
  explicit extractor version.

Verification: derived DB counts, integrity/FK, sampled audit record and full
test suite. Formal build promotion is a separate reversible step.

## Risks

- Transcript text may exceed context limits for long meetings. Fail closed and
  use a separately versioned deterministic segment-selection rule if observed;
  do not silently truncate.
- LLM-generated monitor thresholds can be syntactically valid but economically
  weak. Audit assumptions separately from outcome/vote correctness.
- A structurally valid extraction is not human-audited. Reports must keep
  `machine_extracted` and `human_audited` distinct.

## Out of scope

- Platform API calls during development.
- Changing the production model or promotion gate.
- Treating transcripts as cutoff-safe historical Case input.
- Silently replacing the human-audited 2022 fixture.
