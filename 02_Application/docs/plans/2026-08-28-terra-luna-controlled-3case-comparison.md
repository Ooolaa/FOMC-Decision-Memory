# Terra–Luna 三案例受控比較執行計畫

## Goal

在同一個新版五階段 harness 下，以最小三案例 cohort 比較 `gpt-5.6-terra` 與 `gpt-5.6-luna` 的政策方向、dissent、semantic validity、cache、延遲與實際 token 成本；不以單場結果直接 promote 模型。

## Verified assumptions

- Harness 固定為 runtime document-ID enum、repair allowlist，以及 `medium/high/high/high/medium` stage effort。
- 三案例皆可建立 5 文件、6,816 筆一般 snapshot、9 筆政策利率 context 的 cutoff-safe bundle。
- Cohort 預先固定為：
  - `FOMC-2022-03-15`：HIKE，實際 1 dissent。
  - `FOMC-2023-09-19`：HOLD，實際 0 dissent。
  - `FOMC-2024-09-17`：CUT，實際 1 dissent。
- 2022-03-15 的 remediated Luna 結果已存在；受控比較只需補跑同 harness Terra。另兩案例各跑 Terra 與 Luna，共新增 5 runs。
- 免費 dry-run 的新增五 runs 理論快取成本為 US$2.9868；全部 cache-write 字元估算為 US$9.1569。這些不是實際 usage。

## Dependency map

`fred_fomc_real.sqlite` + `fomc_simulation.sqlite`
→ immutable per-case bundle
→ same `decision_memory.llm_sample` runner and stage schema
→ Terra/Luna append-only run artifacts
→ existing deterministic evaluator
→ per-case paired comparison and aggregate three-case report
→ new immutable offline build manifest

## Milestones

1. Freeze cohort and bundle hashes.
   - Verify each model receives byte-identical bundle content for a case.
   - Revert by discarding only newly created bundle artifacts before any paid call.
2. Preflight models, credentials, output paths and DB backup.
   - Verify exact Terra and Luna IDs are visible and Process/User key fingerprints match without printing secrets.
   - Stop before paid inference on any failure.
3. Execute five new runs sequentially under one aggregate hard cap.
   - Order: 2022 Terra; 2023 Terra then Luna; 2024 Terra then Luna.
   - After each success, subtract actual token-priced cost from the remaining aggregate cap.
   - Pass the remaining aggregate cap to the next runner; on any failure or cap block, stop the entire batch.
4. Evaluate and compare.
   - Report each case separately plus paired aggregate policy accuracy, dissent precision/recall/F1, semantic pass, repairs, cache, latency and cost.
   - Preserve the 2022 Luna cache-gate failure; do not average it away without disclosure.
5. Verify and freeze.
   - Run full tests, source/app SQLite integrity and FK checks, verify append-only row counts, then generate a new immutable artifact manifest.

## Success criteria

- Exactly three preregistered cases and one run per model per case under the same harness.
- No current-meeting statement, minutes, outcome or votes enter model input.
- All completed runs pass schema and semantic validation; failures remain failures and are not replaced silently.
- Aggregate actual token-priced cost stays within the user-approved total hard cap.
- Promotion conclusion remains `INSUFFICIENT_FOR_FROZEN_PROMOTION` unless separately approved evaluation criteria are met; three cases are an engineering comparison, not a statistically powered gate.

## Risks

- Prompt cache misses can materially increase cost; the aggregate cap and sequential stop rule bound exposure.
- One failed run may leave exact partial usage unavailable locally; the batch stops immediately and the remaining cap is not reused.
- Three cases cover action classes but not the full distribution of regimes, participants or dissent behavior.
- Existing 2022 Luna and new 2022 Terra are controlled on harness and bundle, but remain separate stochastic executions.

## Out of scope

- Frozen 45, Self-Harness promotion, ablations, date probe and batch DecisionTrace extraction.
- Repeated runs for sampling variance or formal statistical superiority.
- Prompt, schema, evaluator, cohort or reasoning-effort changes after the first paid call.

## Rollback

The source DB is read-only. Before execution, copy the app DB to a versioned backup. New run/evaluation rows and artifacts are append-only; rollback for presentation means point `FOMC_APP_DB` and the build manifest to the pre-comparison backup/version rather than deleting history.
