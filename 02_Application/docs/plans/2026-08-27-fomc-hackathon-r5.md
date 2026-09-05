# FOMC 決策記憶系統 Hackathon MVP R5 實作計畫

## Goal

依 `FOMC_決策記憶系統_Hackathon_MVP_開發計畫_R5.docx` 建立可離線展示的三頁 MVP：

1. Decision Replay：重播 cutoff-safe FOMC／企業決策。
2. Assumption Monitor：用預註冊規則重算 recognition、action 與 response lag。
3. Simulation & Evidence：顯示合成討論、Chair 提案、投票及 baseline／ablation 證據。

完成證據必須包含 R5 的資料、洩漏防護、synthetic 標示、企業實際 review 事件、離線 cache 與評估要求；不能以只有 UI 骨架或單一成功案例宣稱完成。

## Verified assumptions (updated 2026-08-28)

- `fred_fomc_real.sqlite` 可用 SQLite `mode=ro` 開啟，SHA-256 為 `a7fd78ff8cb52eca2a81ea6b9777bdf048711f2ed6c0a0a660d6d3a777527960`。
- 來源庫 `integrity_check=ok`、`foreign_key_check=[]`，含 22 series、166 meetings、1,541,111 meeting snapshot rows，cutoff 違規 0。
- `DFEDTAR`、`DFEDTARU`、`DFEDTARL` 已入庫；24 場 pre-range 與 142 場 target-range 會議均有政策利率起點。
- R5 的利率約束 episode 可由目前會議表重算為 55＋15＝70 場：`2009-01-27..2015-10-27` 與 `2020-04-28..2022-01-25`。
- 已由 Federal Reserve 官方 URL 取得並 hash 固定 330 份 statement/minutes；另完成 50 場 transcript sample（8,380 頁、11,876 speaker segments、9,269 roster-resolved）。current 45 為 90 份、training 2006–2020 statement/minutes 為 240 份，兩個官方缺件例外已明列。
- 目前目錄不是 Git repository；所有來源庫更新前必須製作具名備份，新增 app DB 可直接刪除重建作為 rollback。
- 開發期資料處理已明確改走 ChatGPT 訂閱登入，Platform API fallback 未授權；正式上線仍保留 Responses API runner，屆時重新校準成本。

## Dependency map

```text
fred_fomc_real.sqlite (source truth, read-only at runtime)
  -> cutoff-safe case manifest
  -> compact policy_rate_context
  -> Decision Replay / simulator input

official meeting documents + participant/outcome labels
  -> document_source / participant / meeting_outcome
  -> DecisionTrace / assumptions / evidence
  -> replay, lag evaluator, persona profiles

metric_spec (frozen deterministic rules)
  -> contradiction / statement flip / rate-only response
  -> evaluation_result
  -> Assumption Monitor

fomc_simulation.sqlite (derived/app state)
  -> review events / runs / scores / cache manifests
  -> three-page offline UI
```

## Milestones

### M0 — Reproducible foundation

Deliver:

- A dependency-free Python package layout, app-schema creator and test command.
- Versioned `metric_spec/` with the exact rate-only episode boundaries and policy series regime rules.
- A read-only source DB preflight that reports hashes, integrity, series coverage and cutoff invariants.

Verify:

- Unit tests pass without network or API keys.
- Preflight fails on writable source connection, missing series after the policy update, cutoff leakage, integrity errors or checksum mismatch.

Rollback:

- Remove only newly created package/spec/test files; source DB remains unchanged.

### M1 — Policy-rate input and compact context

Deliver:

- Add `DFEDTAR`, `DFEDTARU`, `DFEDTARL` to the official update list.
- Back up and strictly update the real source DB.
- Build at most nine prompt records per meeting: current regime/range with `regime_duration_days` plus the latest eight change events.
- Keep the current meeting outcome as a separate label; never include it in the input context.

Verify:

- 22 series, integrity/FK clean, 24 pre-range meetings covered.
- Every context uses observations visible by cutoff and has at most nine records.
- General snapshot block remains capped at 6,816 rows; compact policy context is separate.

Rollback:

- Restore the named pre-update SQLite backup and its recorded hash.

### M2 — Decision-memory app database and enterprise fixture

Deliver:

- Minimal `fomc_simulation.sqlite` schema for document, participant/outcome, DecisionTrace, assumption/event, policy context, model, run and score records.
- One explicit `domain=enterprise_demo`, `synthetic=1`, `composite=1` fixture linked to `BAA10Y`.
- User actions that persist `review_requested_at` and `reviewed_at`; workflow recognition lag is recomputed from stored events.

Verify:

- Schema/FK tests and append-only event tests pass.
- Offline create -> request review -> mark reviewed -> recompute lag works end to end.

Rollback:

- Delete and rebuild the derived app DB from migrations/fixtures.

### M3 — Documents, participants, outcomes and DecisionTrace

Deliver:

- Hash-addressed ingestion with `publication_at`, `usage_class`, `meeting_id` and source provenance.
- Separate members from meeting participants; persist voter/Chair/effective-date fields.
- Multi-vote parser covering the five two-stage-vote meetings.
- DecisionTrace batch extraction with explicit synthetic/source boundaries.

Verify:

- No document after a case cutoff enters its manifest.
- Vote totals balance; participant/evidence IDs resolve; five golden vote cases pass.

Rollback:

- Rebuild only derived document/participant/outcome tables from immutable manifests.

### M4 — Deterministic lag evaluator

Deliver:

- Frozen contradiction rules and statement phrase-set regex.
- `rate_only_response_v1`: only `DFEDTAR` or target-range deltas close policy response events.
- Recognition lag, action lag, response lag and censoring status with rule-version provenance.

Verify:

- Exact 55＋15＝70 episode checksum.
- Recognition remains observable for statement-eligible cases; non-rate tool text never closes action events.
- Golden cases include a success, a false alarm and the 2021-to-2022 liftoff path.

Rollback:

- Pin the prior metric-spec version; derived metric rows are reproducible and replaceable.

### M5 — Reaction model and frozen simulation

Deliver:

- Ordered-logit coefficient cards and pooled baseline trained only on allowed splits.
- Frozen structured simulator flow: profiles -> discussion -> Chair proposal -> vote -> semantic validation.
- Stable shared prefix and per-case stage affinity; usage/cost/latency captured.

Verify:

- Coefficient signs and split rules pass tests.
- One meeting runs end to end; vote counts balance; all outputs are marked synthetic.
- Five-document/one-meeting preflight establishes actual cost before batch execution.

Rollback:

- Select the previous model/prompt/schema hash bundle; runs remain append-only.

### M6 — Evaluation and offline cache

Deliver:

- Majority, persistence, pooled-reaction and naked-frozen-LLM baselines.
- Named/anonymous, reaction/no-reaction and date-probe ablations.
- Policy/vote metrics, dissent precision/recall/F1/base rate, false alarms and lag decomposition.
- Cached one-case demo plus frozen aggregate evaluation artifacts.

Verify:

- Final-test manifests and metric spec are frozen before the one allowed run.
- At least one success and one failure are visible; no LLM is the sole evaluator.
- Full demo remains usable with network disabled.

Rollback:

- Switch the active artifact manifest to the previous hash; caches are immutable.

### M7 — Three-page product and submission package

Deliver:

- Decision Replay, Assumption Monitor, Simulation & Evidence.
- Four-minute flow, 90-second backup video, fixed JSON/screenshots and one-command restart.
- `RUNBOOK.md`, source/app/artifact hashes and submission checklist.

Verify:

- Three rehearsals: normal, API failure, UI restart.
- A reviewer can answer who it serves, how assumption failure is detected, and why it is not memorization.

Rollback:

- Launch the last frozen offline build and artifact manifest.

## Risks and controls

- Missing documents: fail closed on local-file claims; use official URLs with recorded publication dates and hashes when authorized.
- Source DB mutation: backup first, validate resolved paths, integrity/FK/hash after update, and use read-only mode at runtime.
- Leakage: every case manifest records cutoff and rejects later documents/observations; synthetic output never becomes source evidence.
- Cost: no paid batch until the account model, official price, five-document usage sample and explicit cap are known.
- Schedule: follow R5 scope-cut ladder; never cut DecisionTrace, assumption monitor, cutoff checks, synthetic labels or offline fallback.

## Out of scope for Hackathon MVP

- Fine-tuning, reinforcement learning, complete IRL or production Self-Harness promotion gates.
- QE/Operation Twist/taper/forward-guidance tool-action regex before the frozen R5 milestone.
- Cloud multi-user deployment, live uncached 19-agent demo or claims of real-customer outcomes.

## Completion audit

The goal is complete only after every R5 checklist item has a named artifact and direct verification result. A passing unit-test subset, a UI mock, an empty schema or a single successful simulation is insufficient evidence.

## Implementation status (2026-08-29)

- **M0–M2：完成。** 來源 preflight、22 series、compact policy context、app DB、完整企業 synthetic/composite DecisionTrace 與 review workflow 均有測試。正式 app DB 預設唯讀；review 寫入只允許在明確指定的副本上啟用。
- **M3：v5 資料、抽取與人工抽查包完成，真人 QA 待辦。** 330 份 statement/minutes、50 份官方 transcript、166 場 outcome、修正版 1,736 筆逐人投票（103 筆 dissent）與 50/50 場 subscription DecisionTrace extraction 已完成；166 場已知 voter roster 與 vote labels 零落差。roster 是會前已知輸入，核心預測目標是每位 voter 的 `FOR`／`AGAINST`、異議者與最終政策。transcript v3 與 atomic monitor contract 全部通過確定性重驗，但仍為 `PENDING_HUMAN_REVIEW`，未寫入正式 app DB。不可事後挑樣本的 12 場抽查 manifest 已凍結，含全部 5 場 semantic repairs、兩種 attribution flag representative 與 5 場 STANDARD。多投票 gate 已機械重現 4 文件／8 blocks／5 meeting mappings、121 場政策票與 0 parse errors。
- **M4：完成。** 2021–2022 golden path 為 217／91／308 天；165 份 statement 規則稽核完成。rate-only censoring audit 重現兩段約束期 55＋15＝70，Frozen 45 中 9 場受利率約束；recognition 不設限，action/response 的 informative censoring 已揭露。
- **M5：技術流程與 Hackathon feature contract 完成。** 五階段 runner、stable prefix、sequential affinity、一次 semantic repair、usage/cost/cache 與 hard cap 已實作。使用者已明確核准 v1 pooled model 以 `credit_spread_baa10y`／BAA10Y 作為 Hackathon R5 的窄義信用情勢 proxy，並要求揭露不是 NFCI。契約沒有改變模型權重或 bundle hash，因此既有 reaction-dependent variants 不需重跑；production 比較另行凍結。
- **M6：完成。** date-only、naked、named/no-reaction、named/reaction、anonymous/reaction 均為 45/45；anonymous/reaction 僅從 32/45 checkpoint 續跑 13 場，總計 226 subscription requests、1 次 repair、Platform API 0 次／US$0。八列矩陣已通過所有 run hash 與 aggregate 重算驗證；named/reaction 的 dissent F1 為 0.3111，匿名版本為 0.1176。
- **核心投票 UI：完成。** Simulation 將 roster 明列為會前已知輸入，逐位比較 predicted／actual `FOR`／`AGAINST`，並顯示 dissent TP／TN／FP／FN、漏判與誤報；Anonymous 模式只顯示匿名 ID。2022-03-15 demo 為 8/9 且漏判 James Bullard，失敗不被政策方向正確或 roster coverage 掩蓋。
- **M7：產品程式完成，最終 current-code 走查待辦。** 三頁 UI、來源／文件／Case bundle hash 顯示與 disposable-copy review workflow 已由自動測試驗證；v13 的 API failure/restart Edge rehearsal 保留為 rollback 證據，但目前 UI 已新增 provenance／唯讀提示，須等完整矩陣後重新截圖與重跑。90 秒影片、三次 4 分鐘口述計時及第二人 sign-off 尚未完成。
- **最終 gate：已實作，仍正確 BLOCKED。** `decision_memory.submission_gate` 機械檢查來源／正式 DB、transcript v3 candidate 的逐人投票完整性、BAA10Y contract、DecisionTrace corpus／sample／真人 results、五變體、八列矩陣、current-code UI rehearsal、v14 與最終 signoff。目前 9/12 通過；current-code Edge 三模式演練已 PASS，剩餘 3 項為 v5 人工抽查結果、其後才能建立的 v14，以及最終 signoff，不會被自動補造。

目前全套驗證：`python -m unittest discover -s tests -p "test_*.py"` → `Ran 177 tests in 34.893s`、`OK`。來源 DB SHA-256 為 `a7fd78ff8cb52eca2a81ea6b9777bdf048711f2ed6c0a0a660d6d3a777527960`；正式 app DB SHA-256 為 `83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c`。兩庫均為 `integrity_check=ok`、foreign-key violations = 0。
