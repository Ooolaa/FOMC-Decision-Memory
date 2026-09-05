# Terra–Luna votes-only 受控比較計畫

## Goal

在不重跑 profiles、openings、options 與 Chair 的前提下，固定同一份 cutoff-safe case bundle 與同一份 pre-vote context，只替換最後的 `gpt-5.6-terra`／`gpt-5.6-luna`，比較參與者層級 dissent precision、recall、F1、成本、延遲與語意有效性。

## Assumptions

- 使用既有三場 cohort：`FOMC-2022-03-15`、`FOMC-2023-09-19`、`FOMC-2024-09-17`。
- 每場以新版 harness 產生的 Terra 完成 artifact 作共同 anchor；這反映目前 active-model workflow，兩個 vote 模型接收 byte-identical locked context。
- Locked context 僅含 anchor 的 profiles、discussion 與 Chair final proposal。既有最終 artifact 沒有保存 options 與 opening evidence IDs，因此這是新的 votes-only instrument，不宣稱重播原五階段 votes prompt。
- 現階段先準備每模型每場一次的 6-request 比較；沒有新的美元硬上限前不送出 API request。

## Dependency map

`case bundle + Terra anchor artifact`
→ `locked pre-vote context + hash`
→ `single votes Structured Outputs request`
→ `existing semantic validator`
→ `existing policy/vote evaluator in read-only mode`
→ `paired votes-only comparison artifact`

## Milestones

1. 新增 votes-only runner。
   - 在任何 request 前驗證 bundle、anchor meeting、bundle hash、roster、Chair proposal與輸出路徑。
   - 使用模型專屬 votes-only confirmation 與單次語意 repair；schema/refusal/incomplete 直接失敗。
2. 新增回歸測試。
   - 證明只呼叫 votes 一次、兩模型可共用相同 locked-context hash、hard cap 可在 request 前阻擋、非 votes 輸出與 roster 錯誤會 fail closed。
3. 產生 dry-run 成本包絡與不可變 experiment spec。
   - 明列 Terra/Luna 每 case 上界、六個 requests 合計上界與建議總硬上限。
4. 等待使用者另行核准明確美元上限。
   - 核准後才依 case 連續執行 Terra、Luna；任一失敗立即停止且不自動重試或替換。
5. 離線彙整。
   - 報告 participant-level confusion matrix、precision/recall/F1、每場 dissent count、成本與延遲；不直接 promotion。

## Verification

- 新測試先失敗、實作後通過，再執行完整 test suite。
- Dry-run 必須標示 `NO_API_CALL`，並驗證 User-scope key 未被讀取。
- Paid 執行前再次驗證精確模型 ID、User/Process fingerprint、輸入／anchor hashes、DB integrity 與輸出不存在。

## Risks

- 三場只有兩個真實 dissent，participant-level 指標仍是小樣本。
- Terra anchor 可能帶有 anchor-model framing；它對兩個 vote 模型相同，但結果只解釋為 conditional votes quality。
- Votes-only 的第一個 request 沒有同 model 的 cache warm-up，實際成本不可沿用五階段中最後一個 cache-hit votes request。
- 一次 stochastic run 不估計 sampling variance。

## Out of scope

- Frozen 45、前四階段重跑、anchor-model 交叉實驗、三次 repeats、Self-Harness promotion 與現有 app DB 寫入。

## Rollback

Paid 前不修改兩個 SQLite。新 runner、測試、spec 與 artifacts 都是獨立檔案；若不採用，可停止引用新 votes-only instrument，既有 v12 build 與六個完整 harness runs 保持不變。
