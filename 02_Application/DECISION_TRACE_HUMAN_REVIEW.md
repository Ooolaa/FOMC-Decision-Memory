# DecisionTrace 人工抽查操作說明

> 目前狀態（2026-09-01）：Nik 已完成凍結新版樣本 12/12 場，全部六項檢查為 `true`、決定均為 `PASS`，並提供 `I_AM_A_HUMAN_REVIEWER` attestation。正式結果位於 `artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_results_v1.json`，validator 輸出 `formal_import_gate=PASS`。此結論只涵蓋 12 場樣本。

本文件只負責人工覆核交接，不宣稱模型輸出已由人類驗證。不可由 Codex、另一個 LLM 或確定性 validator 代替 reviewer 簽名。

## 已凍結的抽查樣本

- 樣本 manifest：`artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json`
- 樣本檔案 SHA-256：`93a44911021774106cb51a01da01afd798d3c9130ec9758911178df54a9f9436`
- 樣本 payload hash：`028c259ed6ad2383e3ce67d38ad8672e3916fda357c834e249c6cbd15eb555ea`
- 母體：50 場已通過確定性重驗；`qa_queue.json` 保留抽樣前的 `PENDING_HUMAN_REVIEW` 狀態，人工結果另存且已完成 12-case 樣本 gate。
- 樣本：12 場，包括全部 5 場 semantic-repair cases、兩種 attribution QA flag 的 hash-ranked representative，以及 5 場無 flag 的 STANDARD cases。
- 選樣只使用 QA queue hash、meeting ID 與既有 flags；沒有讀取人工評分，也不能在看過結果後換樣本。

舊版 v4 的 12 場人工結果是修正需求的歷史證據，不得自動套用到本次重新抽取的 v5 內容；本文件以下只指向 v5。

重新驗證 manifest：

```powershell
python -m decision_memory.human_review_sample `
  --qa-queue artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/qa_queue.json `
  --target-count 12 `
  --output artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json
```

若來源 QA queue、run artifact 或選樣規則改變，命令會拒絕覆寫既有 manifest。這時必須建立新版本，不能修改 v1。

## 每場審核方式

這是「歷史資料／證據抽取 QA」，不是「預測與會者名單」評分。每場 voter roster 是會前可取得的已知輸入，不列為預測目標；核心預測目標另以每位已知 voter 的 `FOR`／`AGAINST`、異議者與最終政策衡量。`participant_attribution_supported` 只檢查歷史發言是否歸到正確人物，若失敗代表訓練／回放資料需修正，不得解讀為投票預測失敗。

正式 `fomc_simulation.sqlite` 在人工 gate 前刻意保持已凍結舊 hash，不能拿它的舊逐人投票列覆核 v5。`decision_and_vote_match_labels` 必須對照本輪輸入候選庫 `fomc_simulation.transcript_segmentation_v3_candidate.sqlite` 的 `meeting_outcome` 與 `participant_vote`；該候選庫的機械 gate 要求 166 場、1,736 筆逐人投票、103 筆 `AGAINST`／dissent，且每場已知 voter roster 與逐人投票集合完全相符。

1. 由 manifest 的 `run_artifact` 開啟該場 DecisionTrace。
2. 依 `evidence_id`、文件 locator／excerpt 與 transcript speaker attribution 回到對應來源。
3. 逐項填寫：
   - `context_summary_supported`
   - `options_and_debate_supported`
   - `participant_attribution_supported`
   - `decision_and_vote_match_labels`
   - `assumption_is_falsifiable_and_monitorable`
   - `no_post_cutoff_or_synthetic_source_leakage`
4. `case_decision` 只能是 `PASS`、`FAIL` 或 `NEEDS_CORRECTION`。
5. reviewer 必須填入真實姓名／識別、ISO 8601 時間與具體 notes；不得只填「看起來正確」。

## 結果檔契約

不要編輯 sample manifest。另建
`artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_results_v1.json`，至少包含：

可先複製已預填 12 個 meeting ID、run artifact、sample SHA-256 與六項 checklist 的
`submission_templates/decision_trace_human_review_results_v5_atomic_monitor.json`。只能修改複本，不要修改
模板或 sample manifest；所有 `__FILL_...__` 都必須由真人完成，checklist 值要改為 JSON
boolean `true`／`false`，不是字串。模板本身刻意無法通過 validator，避免被誤當成簽核。

```json
{
  "schema_version": "decision_trace_human_review_results_v1",
  "sample_manifest": "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json",
  "sample_manifest_sha256": "填入實際檔案 SHA-256",
  "human_reviewer_attestation": "I_AM_A_HUMAN_REVIEWER",
  "review_status": "APPROVED_SAMPLE | COMPLETE_WITH_FINDINGS",
  "reviews": [
    {
      "meeting_id": "從 sample manifest 選取",
      "reviewer": "人工 reviewer",
      "reviewed_at": "ISO 8601 timestamp",
      "case_decision": "PASS | FAIL | NEEDS_CORRECTION",
      "checklist_results": {
        "context_summary_supported": true,
        "options_and_debate_supported": true,
        "participant_attribution_supported": true,
        "decision_and_vote_match_labels": true,
        "assumption_is_falsifiable_and_monitorable": true,
        "no_post_cutoff_or_synthetic_source_leakage": true
      },
      "notes": "具體依據或需修正內容"
    }
  ]
}
```

完成 12/12 場後執行 fail-closed validator：

```powershell
python -m decision_memory.human_review_results `
  --sample artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json `
  --results artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_results_v1.json
```

validator 會驗證 sample 檔案 SHA-256、12 場集合、重複／缺漏、真人 attestation、含時區的 ISO 8601 時間、六項 checklist、決策與 notes。只有輸出 `formal_import_gate=PASS` 才代表 sample gate 通過；有任一 finding 時命令以非零 exit code 結束。

## 通過與入庫邊界

- 12/12 場均完成前，整批保持 `PENDING_HUMAN_REVIEW`。
- 任一 `FAIL` 或 `NEEDS_CORRECTION` 都不得匯入正式 app DB；先修正／重抽該場並以新 artifact hash 重新審核。
- 只有人工結果完整、sample hash 相符且所有 case 為 `PASS`，才可另行核准正式匯入。
- 人工抽查通過只代表這 12 場；不可宣稱 50/50 場已逐場人工審核。
