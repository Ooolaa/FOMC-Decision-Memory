# Hackathon Submission Record

Status: **PRE-SUBMISSION / NOT YET SUBMITTED**

Current build status: **R5 TECHNICAL MVP READY / FORMAL SUBMISSION DEFERRED**  
Technical gate is 11/11 `PASS`. The full submission gate is 11/12 and only lacks the real-world `submission_signoff`, which is deferred by user instruction. No Platform API fallback was used for the five development variants.

## Frozen technical evidence

- R5 plan SHA-256: `29ef4686048b0b3f9c2e2376d6e634b045e5986bc3192f5a177dba7004a9b361`
- Source DB SHA-256: `a7fd78ff8cb52eca2a81ea6b9777bdf048711f2ed6c0a0a660d6d3a777527960`
- Formal app DB SHA-256: `83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c`
- Prior immutable rollback manifest: `artifacts/manifests/hackathon_r5_offline_build_2026-08-28_v13.json` (not the final manifest for the current code/artifacts)
- Current v16 portable manifest: `artifacts/manifests/hackathon_r5_offline_build_2026-09-01_v16.json`
- Current manifest payload hash: `28ceabd494f88659009c6e4cd7ecd5778ff63c2a7dd0141813a2c05bde7e7706`
- Manifest root policy: `workspace_relative_portable` (`root=.`)
- Paid sample artifact SHA-256: `180ca8660c02604ed5137b3a3749d1c5d7fa5c6cdfc8702c4f6f568077896a7f`
- Paid sample result: `gpt-5.6-terra`, US$0.99421 token-priced cost under US$5 hard cap; policy HIKE correct, dissent F1 0 on the single case
- Luna comparison audit SHA-256: `6818b4279cb1ce0a0d9b711f4169a61936f99c2df8b3341a18c8bdd50fb98710`
- Luna comparison result: failed closed in profiles after one evidence-ID semantic repair under a US$1 hard cap; no later stages, DB run, evaluation or Frozen 45 execution; exact billed cost was not persisted
- Luna remediation sample SHA-256: `914311d47c142786d4b7959e2ba589c32ca3cb0d3e198075706995e6787043ba`
- Terra-Luna comparison SHA-256: `16ce23e0b1363adfb2f16ff4374c4f81f5bd27300b4cc274842efafc8d8fb0b8`
- Luna remediation result: five stages completed without repair for US$0.17550248 under a US$1 cap; HIKE correct, 9-0 predicted, dissent F1 0, cache gate failed; not a controlled model-only comparison
- Controlled three-case spec SHA-256: `203072211a5e123e31ac258aa2ad8fd6e36a1a6bc05d8764a68f18c4f6cef9d8`; cap amendment SHA-256: `8cf9bcf64d191e1743ae5566fbfc4c44459fa7d4c3263f9363c7f9a8af7f94de`
- Controlled three-case comparison SHA-256: `f850a99465d0729414dbdc043e190464ebabf73ef89b755b921727f9811ad910`
- Controlled result: both models achieved policy 3/3; Terra dissent precision/recall/F1 = 1.000/0.500/0.667, Luna = 0.167/0.500/0.250; all six runs passed semantic validation without repair and all six failed the cache gate
- Controlled cost: five newly authorized runs cost US$8.86148352 under the US$14 aggregate hard cap, leaving US$5.13851648; Terra three-case cost US$8.2078562 and Luna US$0.8291298
- New run SHA-256 values: `1654f6e35632fa14a805c8f438ea51c93b684d49b0a0c08e9f84617a348d1e81`, `8ece07188540c59265fd47fe4b142cd23989e7064350983724ec44bbda1f6454`, `f182d63e45e7a1fe6123e2987a905773627bb7897f8f160ac36683719b76d096`, `6be6c4c388dd3bf8e07b2c62d753989116bb03b297fa2bc011f560283547fd7f`, `89ce26d240a7efe54e2843db966ec4f72f19a459d9905ba86cf169e1232892b3`
- Votes-only spec SHA-256: `4b168e55e371fcec5b4fc61301ccea62b9b2953289d0015a7b5f6a54c67f3c18`; authorization SHA-256: `d99aa74042a3ffe7cb113827b0d65f75a85c6f7ee1330f77afc130b141f8b102`
- Votes-only comparison SHA-256: `db657b2fbdac44d4d5c468ee69f6c0dc120f533558e60174846f9f1a4527cbf9`
- Votes-only result: locked Terra profiles, discussion and Chair proposal for both models; Terra dissent precision/recall/F1 = 1.000/0.500/0.667, Luna = 0.250/0.500/0.333; Luna produced three false-positive dissents in the unanimous 2023 HOLD case; all six requests completed without repair
- Votes-only cost: US$3.89411955 under the independent US$6.50 aggregate hard cap, leaving US$2.60588045 unused and not reallocated; Terra cost US$3.5399745 and Luna US$0.35414505
- Votes-only run SHA-256 values: `2f9a2749228248c50f8e274a318c0d45f170cdff22194db9516efb7788973aa4`, `ef76092458961c0f2a4d7816c2dd54ffbcb454b18725243040a0830041c15787`, `b3e0674631dabdc0a10caa06c0edcea35ace322adf0304622e775597078c0e2e`, `a9adc4293661b9a6b9cb5ccdd8841cb181346862b59bffa69b50e35d8f2e8c1f`, `c339971d4a90e27ec20292d99fd7233b51d69e00a680592614bd1a2e314fe7ae`, `6b2fb236a049277756b19483c6fbc1dc2578d3b87aa78e071f8048fcaeaf3766`
- DecisionTrace subscription batch: 50/50 completed and 50/50 passed deterministic revalidation. Source DB SHA-256 `a7fd78ff8cb52eca2a81ea6b9777bdf048711f2ed6c0a0a660d6d3a777527960`; formal app DB SHA-256 `83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c`.
- DecisionTrace human-review sample: 12 immutable cases; sample payload hash `028c259ed6ad2383e3ce67d38ad8672e3916fda357c834e249c6cbd15eb555ea`, sample file SHA-256 `93a44911021774106cb51a01da01afd798d3c9130ec9758911178df54a9f9436`. Nik completed 12/12 `PASS`; results SHA-256 `449b0bc91bba5b15adf102ad0a2314090a6efdaa880eaf881b9c927de57b7de0`. This is sampled-case evidence only.
- Human-review results gate: `decision_memory.human_review_results` requires the exact 12-case set, sample file hash, human attestation, timezone-aware timestamps, complete boolean checklists, allowed decisions and notes. It opens only the sampled-case gate and does not auto-import or claim 50-case human review.
- Complete subscription variants: date-only 45/45 (policy accuracy 0.9111); naked 45/45 (policy accuracy 0.9778, dissent F1 0.1071); named/no-reaction 45/45 (0.9333, 0.2857); named/reaction 45/45 (0.9778, 0.3111); anonymous/reaction 45/45 (0.9778, 0.1176).
- Anonymous/reaction resumed exactly 13 pending cases from its 32/45 checkpoint and finished with 226 subscription requests, 1 repair, Platform API calls = 0 and Platform API cost = US$0.
- Complete variant integrity: all referenced run SHA-256 values match; each case array, aggregate and checkpoint count is 45; every aggregate was independently recomputed within floating-point tolerance by the matrix builder.
- Multi-vote parser audit SHA-256: `6aec1319367bd10e14b315afae914fb5c56586baf2f24008d6a6769b47af6f70`; 4 source documents / 8 vote blocks / 5 mapped meetings / 121 parsed policy meetings / 1,219 votes / 0 errors.
- Rate-only censoring audit SHA-256: `26dc17f2771004c77bcd8cffa8e06f07bfc76d386d000ad6f34e79a022aeaf4e`; episode counts 55+15=70, Frozen 9/45 constrained and 36/45 with observable rate capacity, split manifest hash `0f15dda445112ec637e85b8cfe99f195ac0ed9e1d5a8f79e1be375b0420cee14`.
- Subscription development billing route: `chatgpt_subscription`; Platform API calls = 0, Platform API cost = US$0 for all five variant batches and the DecisionTrace batch.
- Final eight-row matrix: `artifacts/evaluation/r5_subscription_variant_matrix_v1.json`, SHA-256 `d3ad48b9f7fa17320c46e36447ba15b188e3d4320f589b3aaedd5fcc79c3cea0`; status `EVALUATION_MATRIX_COMPLETED`, 8 rows, n=45, Platform API calls = 0 and cost = US$0.
- Final current-code UI rehearsal: `PASS`, 3 modes／4 views, rehearsal SHA-256 `dbfb2ae578f3e0fe46af6f03be904c456bed04a4e36436739c3807db0a7d513b`.
- Current v16 portable immutable manifest: 140 files, manifest payload hash `28ceabd494f88659009c6e4cd7ecd5778ff63c2a7dd0141813a2c05bde7e7706`.
- Current complete test result: `Ran 246 tests in 50.931s` / `OK`.
- Current database checks: source/app `PRAGMA integrity_check=ok`; source/app foreign-key violations = 0; both database hashes remain unchanged.
- UI provenance gate: Decision Replay displays source/evidence hashes; selected completed variant displays the hash-verified Case bundle; formal app DB review writes are disabled by default and rejected even if the write flag is requested without a copy.
- Reaction-model feature contract: user-approved Hackathon R5 use of `credit_spread_baa10y`/BAA10Y as a narrow credit-conditions proxy, explicitly not NFCI. Contract SHA-256 `b7d72edec62a4c83638b48628086170fd9ec01cac6038e168e8f7249624d3f1d`; existing model weights and bundle hashes are unchanged, so no reaction-dependent rerun is required by this decision.
- Current technical gate: `READY`, 11/11 checks pass, exit code 0.
- Current complete submission gate: `BLOCKED`, 11/12 checks pass. Exact blocker: `submission_signoff`.
- Final UI rehearsal contract `hackathon_r5_final_ui_rehearsal_v1` hash-binds the current app, launcher, complete matrix, three capture reports, four canonical screenshots and 12 per-mode screenshots; validator passed.

## Submission assets

- Organizer copy: `HACKATHON_SUBMISSION.md`
- Four-minute script: `DEMO_SCRIPT.md`
- Canonical screenshots: `artifacts/screenshots/decision_replay.png`, `assumption_monitor.png`, `simulation_evidence.png`, `enterprise_decision_replay.png`
- Review workflow screenshots: `artifacts/screenshots/review_requested.png`, `review_completed.png`
- Ninety-second video filename: **PENDING**
- Repository or archive path: engineering handoff will be regenerated from the v15 technical snapshot; formal organizer archive remains pending.
- Submitted form URL or confirmation ID: **PENDING**

## Required sign-off

- Presenter completed three timed four-minute rehearsals: **PENDING**
- Video was watched end-to-end with audio: **PENDING**
- Synthetic/composite labels visible: **PENDING SECOND-PERSON CHECK**
- No API key, secret or private customer data visible: **PENDING SECOND-PERSON CHECK**
- Download/access permissions tested: **PENDING**
- Contact information checked: **PENDING**
- Reviewer name and timestamp: **PENDING**

This record is intentionally outside the immutable build manifest because it is completed only after the final video, archive and organizer submission exist.
