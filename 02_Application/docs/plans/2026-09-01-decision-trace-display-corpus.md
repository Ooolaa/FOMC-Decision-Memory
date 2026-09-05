# DecisionTrace 展示資料庫與全會議目錄開發計畫

## 目標

保持 `fred_fomc_real.sqlite`、`fomc_simulation.sqlite` 與 transcript v3
candidate 資料庫不變，將通過人工抽樣品質關卡的 50 份 DecisionTrace
artifacts 寫入新的衍生展示資料庫，並讓 Streamlit 可選擇所有 166 場 FOMC
會議，同時明確區分完整重播與基礎政策資料。

## 已驗證假設

- `fred_fomc_real.sqlite` 含 166 場 `fomc_meeting` 與 1,541,111 筆會議時點經濟快照。
- `fomc_simulation.transcript_segmentation_v3_candidate.sqlite` 含 166 場 outcome、1,736 筆逐人投票與 transcript v3 分段結果。
- v5 batch status 為 `COMPLETED`，50/50 run artifacts 均有綁定 SHA-256，凍結抽樣人工複核為 12/12 `PASS`。
- artifacts 的 transcript evidence 與 vote labels 綁定 transcript v3 candidate，因此衍生庫必須由 candidate 複製，不能由正式 app DB 複製。
- candidate 已有 2022-03-15 人工稽核 golden trace；導入 50 場 batch 後，完整 FOMC replay 總數為 51 場。

## 依賴關係

```text
fred_fomc_real.sqlite (166 meetings + point-in-time snapshots)
  -> app.py case catalog and economic chart

transcript v3 candidate SQLite
  + batch_status.json and 50 hash-bound run artifacts
  + human_review_results_v1.json
  -> materialize_decision_trace_corpus.py
  -> new derived display SQLite
  -> app.py dynamic replay catalog
```

## 里程碑

### M1 — 回歸契約

- 新增測試，要求匯入器在人工複核未通過、artifact hash 漂移、輸入與輸出路徑重疊或語意衝突時 fail closed。
- 新增 UI 測試，要求會議選單包含全部會議，並證明選取的歷史會議會真正控制重播內容。

驗證：新測試在實作前失敗，實作後通過。

回滾：僅移除新增測試。

### M2 — 衍生資料庫實體化

- 將 transcript v3 candidate 複製到全新輸出路徑；絕不更新任一輸入資料庫。
- 驗證 batch summary、50 個 artifact hash 與 12/12 人工抽樣結果。
- 重用 `persist_fomc_decision_trace` 進行 schema、引文、與會者歸屬、outcome、vote 與 monitor series 驗證。
- 以單一 transaction 寫入 50 份 trace，然後執行 `integrity_check`、`foreign_key_check`、預期筆數與輸入資料庫 hash 驗證。

驗證：輸出庫含 51 場 FOMC trace（50 batch + 1 golden）、166 場 outcome 與 1,736 筆投票；兩個輸入庫 hash 不變。

回滾：只刪除具名衍生輸出庫；兩個輸入庫保持 byte-identical。

### M3 — 動態重播介面

- 只在「決策重播」頁顯示會議選單。
- 以新到舊列出全部 166 場會議，標記為「完整 DecisionTrace」或「政策／投票／經濟資料」。
- 將選定的 `meeting_id` 實際傳入 replay loader。
- 只在 trace 存在時顯示情境、選項與辯t論；基礎案例只顯示可驗證的會議日期、cutoff、政策結果、投票與經濟快照。
- 保留下次會議預測與凍結模擬頁的既有語意。

驗證：Streamlit 測試可分別選擇一個完整重播與一個基礎案例，且畫面顯示的會議與政策結果會隨選擇改變。

回滾：還原 `app.py` 與 `run_app.ps1`；衍生庫可保留未使用或刪除。

### M4 — 端到端驗證

- 執行匯入器與 UI 目標測試，再執行完整 repository 測試。
- 執行 SQLite integrity/FK 檢查並確認筆數。
- 以衍生資料庫啟動 app，交互測試早期、中期與最近會議，並包含一個 base-only 案例。
- 從新鮮上下文複核最終 diff，檢查資料洩漏、誤導標籤與意外寫入正式資料庫的風險。

驗證：保留指令輸出、資料庫稽核與 UI 交互證據。

回滾：還原新 checkpoint，並將 launcher 預設值恢復為先前設定。

## 風險

- Transcript artifact 可能通過舊 candidate 驗證，卻與既有 golden trace 衝突。匯入必須 fail closed，不得覆寫任一筆資料。
- 完整重播覆蓋率為 51/166，不是 166/166。UI 不可暗示其餘 115 場具有 transcript-backed 與會者辯t論。
- 會後 transcript/minutes 仍只是擷取與評估證據，不得進入歷史模型 Case 輸入。

## 本次不處理

- 為剩餘 115 場會議產生新 DecisionTrace。
- 將只有 statement/minutes 的摘要當作具名與會者發言。
- 取代或修改任一正式 SQLite 資料庫。
- 變更政策或投票預測模型。
