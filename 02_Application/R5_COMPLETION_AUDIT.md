# R5 Completion Audit

> 歷史狀態說明：本文件保留 2026-08-29 的完成度快照。人工抽樣、current-code UI rehearsal 與後續 immutable manifest 已在 2026-09-01 完成；最新技術結論以 `R5_TECHNICAL_COMPLETION_AUDIT_2026-09-01.md` 為準。正式投稿 signoff 依使用者指示暫緩。

Audit date: 2026-08-29  
Authoritative plan: `FOMC_決策記憶系統_Hackathon_MVP_開發計畫_R5.docx`  
Plan SHA-256: `29ef4686048b0b3f9c2e2376d6e634b045e5986bc3192f5a177dba7004a9b361`

## Verdict

R5 is **not complete**. The data foundation, 50-meeting transcript corpus, deterministic lag benchmark, 50/50 subscription-based DecisionTrace extraction, full synthetic/composite enterprise DecisionTrace, participant profile cards, three-page offline UI, restart path and audit artifacts are implemented. All 50 extracted traces passed deterministic revalidation but remain `PENDING_HUMAN_REVIEW`, so they have not been imported into the formal app database. All five Frozen 45 subscription variants are now complete. The anonymous/reaction run resumed only the 13 pending cases from its verified 32/45 checkpoint and completed at 45/45. Across the five variants there are 225/225 case results and 946 subscription requests; all batches report zero Platform API calls/cost. Every referenced run hash and recomputed aggregate passed the matrix builder, and the complete eight-row matrix is frozen at `artifacts/evaluation/r5_subscription_variant_matrix_v1.json`.

The completed named/reaction variant achieved policy accuracy 97.8% and dissent precision/recall/F1 0.318/0.304/0.311; anonymous/reaction achieved 97.8% and 0.182/0.087/0.118; named/no-reaction achieved 93.3% and 0.316/0.261/0.286; naked achieved 97.8% and 0.091/0.130/0.107. The date-only probe achieved 91.1% policy accuracy and is treated as a memorization warning, not deployable evidence. These are single-run ChatGPT-subscription development measurements, not Responses API promotion runs; they contain no repeats, confidence intervals or sampling-variance estimate.

The current build is an honest offline technical MVP with a complete single-run development matrix, but it is not yet the full R5 competition submission described by the Word plan. The user explicitly approved `credit_spread_baa10y` as the Hackathon R5 financial-conditions proxy. The frozen contract states that BAA10Y is a narrow credit-conditions proxy, not the broad NFCI; it leaves the existing v1 model, prompts, schemas and reaction-dependent bundle hashes unchanged, so no rerun is required for this documentation-only decision. Remaining gates are the real 12-case human review, current-code Edge three-mode rehearsal, v14 manifest and final human submission sign-off.

## Requirement-by-requirement evidence

| R5 requirement | Status | Current evidence | Missing proof or work |
|---|---|---|---|
| Strict point-in-time FRED source, 22 series, 166 meetings | PASS | `decision_memory.preflight` reports 22 series, 166 meetings, 1,541,111 snapshots, zero cutoff violations, integrity `ok` | None |
| DFEDTAR plus DFEDTARL/U policy-rate coverage | PASS | 24 pre-range and 142 range meetings; all three series marked `FRED_ONLY_OBSERVATION_DATE` | None |
| Compact policy context, at most nine records with regime duration | PASS | `policy_rate_context` covers 166 meetings; preflight maximum is 9 | None |
| Rate-only lag specification with exact 55+15 episodes | PASS | `metric_spec/rate_only_response_v1.json`; durable audit mechanically reproduces 55 and 15, plus 9/45 constrained Frozen cases and the identical Frozen split hash | Censoring remains informative; artifact does not claim unbiased event-time estimation |
| Observable recognition golden case | PASS | 2021-05-12 contradiction, 2021-12-15 statement flip, 2022-03-16 rate response; 217/91/308 days; `statement_alert_audit_v1.json` classifies all 165 cached statements | None for deterministic reproduction |
| Statements and minutes with source/hash boundary | PASS | 330 `document_source` rows: 165 statements and 165 minutes | None |
| Five-meeting multi-vote parser gate | PASS | 120 training minutes audited; four documents contain eight vote blocks mapped explicitly to five meeting IDs; 121 policy meetings parsed, 1,219 votes, zero errors | Three non-calendar/non-rate rounds are explicitly excluded with `null`; mapping is source-order and versioned in code/artifact |
| Finite 2006-2020 transcript corpus, speaker segmented | PASS | 50 official PDFs, 8,380 pages and 11,876 segments; 9,269 segments resolve uniquely to the meeting roster; all 50 file hashes match | Publication timestamp is the conservative official page `Last Update`, not a claimed exact first-release timestamp |
| Participant roster, Chair, voting and dissent labels | PASS | 79 participants, 2,982 meeting-participant rows, 1,711 votes, 78 dissents, 166 outcomes | None for the deterministic label layer |
| Batch DecisionTrace extraction | PASS PENDING HUMAN REVIEW | 50/50 traces, 58 requests, 8 semantic repairs; deterministic QA revalidated schema, evidence, attribution, outcome, votes and database hashes. A 12-case immutable review sample includes all 8 repairs, every QA flag category and 2 STANDARD cases; completed results have a fail-closed validator | A real reviewer must complete the separate result file; sample manifest hash is `ad1e5b3d4576df8111a5966570a7eafc4564634ad52189eb989c86cc4ef669bd`. No human results exist yet and artifacts stay outside the formal DB |
| Interpretable ordered-logit reaction model | PASS WITH APPROVED HACKATHON PROXY | 121 training meetings; converged pooled model; 16 readable cards cover the actual 2022-03-15 roster (9 voters). Contract `reaction-feature-contract-hackathon-r5-v1` explicitly approves BAA10Y as a narrow credit-conditions proxy, not NFCI | Approval is Hackathon-only and does not establish production suitability. Cards remain pooled, not independent participant models |
| Four baselines | PASS FOR DEVELOPMENT COMPARISON | Majority, persistence, pooled reaction and naked frozen LLM completed across Frozen 45 | Naked LLM is single-run subscription evidence, not a promotion baseline with repeats |
| Frozen LLM structured simulation | PASS FOR SINGLE-RUN DEVELOPMENT MATRIX | All five variants completed 45/45. Anonymous/reaction resumed only the 13 pending cases, ending with 226 subscription requests, one repair and zero Platform API calls/cost | Single runs do not establish sampling variance or Responses API promotion validity |
| Luna cost comparison | PASS FOR THREE CONTROLLED CASES | Terra cost US$8.2078562 and Luna US$0.8291298 across the same three cases; Luna cost share was 10.10% | One run per case does not estimate sampling variance; all six runs failed the cache gate and no promotion is authorized |
| Votes-only model isolation | PASS FOR THREE CONDITIONAL CASES | With byte-identical locked Terra profiles, discussion and Chair proposal, Terra dissent F1 was 0.667 and Luna 0.333; six requests cost US$3.89411955 under a separate US$6.50 cap and required no repair | Not an exact replay of the original five-stage votes prompt; one run per case, no variance estimate and no Frozen promotion authority |
| Chair proposal and balanced vote | PASS FOR SIX CONTROLLED API RUNS AND 45 SUBSCRIPTION DEVELOPMENT RUNS | Three-case Terra/Luna runs and all Frozen subscription cases produced a Chair proposal and balanced vote structure | Subscription evidence is not API-equivalent promotion proof |
| Named versus anonymous ablation | PASS FOR SINGLE-RUN DEVELOPMENT MEASUREMENT | Both variants are 45/45. Named/reaction dissent F1 is 0.311; anonymous/reaction is 0.118 at identical 97.8% policy accuracy | No repeats, CI or causal claim that names alone explain the difference |
| Reaction versus no-reaction ablation | PASS FOR SINGLE-RUN DEVELOPMENT MEASUREMENT | Named/reaction and named/no-reaction are both 45/45 with per-case policy and dissent metrics | No repeats, CI or promotion authority |
| Date-only memorization probe | PASS WITH WARNING | 45/45; policy accuracy 91.1% | Treat high performance as memorization risk; no dissent output by design |
| Policy evaluation on Frozen 45 | PASS FOR DETERMINISTIC AND FIVE COMPLETE SUBSCRIPTION VARIANTS | Eight-row matrix contains 45 per-case results for majority 62.2%, persistence 82.2%, pooled 57.8%, date-only 91.1%, naked 97.8%, named/no-reaction 93.3%, named/reaction 97.8% and anonymous/reaction 97.8% | Single runs do not establish statistical superiority |
| Dissent precision, recall, F1 and base rate | PASS FOR FOUR COMPLETE FULL-SIMULATION VARIANTS | Named/reaction: 0.318/0.304/0.311; anonymous/reaction: 0.182/0.087/0.118; named/no-reaction: 0.316/0.261/0.286; naked: 0.091/0.130/0.107 | All measurements are single-run subscription development evidence |
| False-alarm evidence for assumption alerts | PASS WITH LIMITATION | 165-statement deterministic audit: temporal false alarms 0/1 pre-contradiction opportunity; support/flip cooccurrence opportunities 0, so that rate is explicitly not estimable | This is a mechanical regex-control audit, not a human semantic false-positive rate; the available denominators are too small for a strong rate claim |
| Enterprise demo bound to BAA10Y | PASS | Full Context/Options/Debate/Decision/Vote replay, deterministic contradiction and composite disclosure | None |
| Real review_requested/reviewed workflow | PASS | Disposable DB copy records `CONTRADICTION -> REVIEW_REQUESTED -> REVIEWED`; default launcher hides write actions and rejects writes against the formal DB | Demo must use an explicitly selected disposable copy |
| Three offline product pages | PASS WITH COMPLETE MATRIX | Decision Replay, Assumption Monitor and Simulation & Evidence render offline; source DB SHA-256, evidence-document SHA-256 and selected Case bundle SHA-256 are visible; all five variant artifacts and the eight-row matrix are hash-verified before display. A current-code Chrome smoke rendered all four required views, showed all eight matrix rows and produced zero browser-console errors | Chrome smoke is development evidence only; current-code Edge screenshots still require the final three-mode rehearsal |
| Normal, API-failure and restart technical rehearsal | PASS FOR V13 ROLLBACK; CURRENT UI REHEARSAL PENDING | Four prior Edge views, including enterprise replay, match by body-text and PNG SHA-256; health failed after stop and returned 200/ok after `run_app.ps1`. A final-rehearsal validator now binds app, launcher, matrix, three capture reports and all mode screenshots | Rerun all technical views after the matrix is complete; a placeholder or self-declared PASS JSON cannot pass the final gate |
| Fixed screenshots and build manifest | PRIOR ROLLBACK ONLY | v13 retains the prior immutable screenshots and manifest; the complete matrix SHA-256 is `d3ad48b9f7fa17320c46e36447ba15b188e3d4320f589b3aaedd5fcc79c3cea0` | Current-code Edge screenshots and v14 are intentionally withheld until the final three-mode UI rehearsal; never alter v13 |
| Ninety-second offline video | NOT DONE | Shot list exists | Record, watch and name the final video |
| Three timed four-minute rehearsals | NOT DONE | Script exists; browser path is tested | Presenter must perform and time all three runs |
| Final submission text | PASS | `HACKATHON_SUBMISSION.md` | Paste into the organizer form and retain submitted copy |
| Final submission record and second-person cross-check | PARTIAL | Hashes and pending fields are recorded in `SUBMISSION_RECORD.md` | Video filename, submitted URL/ID, contact/access check and reviewer sign-off remain pending |
| Machine-readable final submission gate | PASS AS A BLOCKING CHECKER | `decision_memory.submission_gate` verifies 11 independent gates and currently reports 7 PASS / 4 BLOCKED | Remaining blockers are human review results, current-code UI rehearsal, v14 manifest and final submission sign-off |

## Current authoritative counts

```text
document_source:      381 (165 statements, 165 minutes, 50 transcripts, 1 synthetic fixture)
transcript_segments: 11,876 (9,269 roster-resolved; 78.05%)
participants:          79
meeting_participants: 2,982
participant_votes:   1,711 (78 dissent)
meeting_outcomes:      166
decision_traces:         2 in formal app DB (1 FOMC human-audited, 1 enterprise synthetic/composite)
simulation_runs:         8 (one offline cached baseline plus seven paid run records)
evaluation_results:     49 (seven metrics for each paid model run)
DecisionTrace artifacts: 50 extracted cases, all deterministically revalidated, pending human review
R5 variant artifacts:   225/225 case results (all five variants complete)
R5 stage requests:      946 (four 5-stage full variants plus one date-only probe; one semantic repair)
Platform API usage:       0 calls / US$0 for the subscription development batches
```

## Remaining execution order

1. Connect a controllable Microsoft Edge instance and execute the current-code normal/no-key/restart rehearsal at 1440x1100; require matching body-text and screenshot SHA-256 for all four views.
2. Only after the final UI rehearsal passes, create the immutable v14 manifest and rerun the submission gate.
3. A real reviewer must complete the frozen 12-case DecisionTrace sample in `DECISION_TRACE_HUMAN_REVIEW.md` before any formal DB import; retain the current `PENDING_HUMAN_REVIEW` boundary until sign-off.
4. Extend the alert-audit denominators when future preregistered cases become available; do not overstate the current 0/1 temporal result.
5. Record the 90-second video; perform three timed four-minute presenter rehearsals; obtain second-person sign-off.

## Fail-closed interpretation

Until the remaining submission gates are complete, use the phrases **offline technical MVP with a complete eight-row single-run subscription development matrix**, **50 deterministically revalidated DecisionTrace artifacts pending human review**, and **one enterprise synthetic/composite fixture**. Do not claim API-equivalent 45-case promotion evaluation, statistically powered dissent prediction, formal DecisionTrace import, final Edge rehearsal or Self-Harness promotion has been completed.
