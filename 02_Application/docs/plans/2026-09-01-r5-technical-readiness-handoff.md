# R5 技術就緒與工程移交收尾計畫

## Goal

在不執行正式 Hackathon 投稿行政工作的前提下，證明 R5 technical MVP 已完成，將技術 gate 與 submission signoff 分離，更新失真的狀態文件，並產出可驗證的最新工程移交包。

## Verified assumptions

- 使用者明確要求影片、三次彩排、主辦方確認與第二人 signoff 之後再做。
- 正式 `fred_fomc_real.sqlite` 與 `fomc_simulation.sqlite` 不應因本輪狀態整理而改寫。
- 現有 submission gate 的 12 項中只有 `submission_signoff` 未通過；其餘 11 項已有直接 artifact 證據。
- 專案不是 Git repository，因此移交以 allowlist、SHA-256、secret scan 與 verifier 代替 commit/tag。

## Dependency map

```text
R5 Word spec
  -> source/app DB invariants
  -> DecisionTrace + human sample
  -> Frozen 45 variants + matrix
  -> current-code UI rehearsal
  -> immutable v15 manifest
  -> technical gate
  -> engineering handoff bundle

real-world video/rehearsal/organizer confirmation
  -> submission_signoff
  -> full submission gate (deferred)
```

## Milestones

1. **Scope split — completed**
   - 新增 `evaluate_technical_readiness` 與 `--scope technical`。
   - submission gate 預設行為不變，仍會要求真人 signoff。
2. **State correction — completed**
   - RUNBOOK、人工 QA 文件、checklist、submission record 與歷史 audit 均對齊 12/12 樣本結果。
   - 明示 sampled PASS 不等於 50 場逐場人工審核。
3. **Immutable freeze — completed**
   - v15 首次揭露絕對路徑不可攜問題；升版建立 `root=.` 的 v16 portable manifest。任何列入檔案改動都會讓 gate fail closed。
4. **Verification — completed**
   - 244 tests `OK`；preflight、lag spec、vote parser、SQLite integrity/FK 與 technical gate 均通過。
5. **Engineering handoff — in progress**
   - 依 v15 狀態建立新 snapshot，執行 secret scan、manifest/checksum verifier 與 ZIP 完整性驗證。

## Rollback

- technical gate 是新增入口；刪除 wrapper 與 CLI scope 即可回到原 submission-only 行為。
- v14／v15 manifests 保留為歷史快照，不覆寫。
- source/app DB hash 在本輪保持不變，無資料回滾需求。

## Out of scope

- 影片、三次四分鐘彩排、主辦方 submission confirmation、第二人覆核。
- Responses API repeats、信賴區間、Self-Harness promotion。
- 正式 DB 自動匯入、production deployment、雲端多人權限。
