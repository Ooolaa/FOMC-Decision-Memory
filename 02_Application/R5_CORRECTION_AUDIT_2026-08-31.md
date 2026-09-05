# R5 DecisionTrace 修正稽核（2026-08-31）

> 後續狀態：凍結的新版 12-case 樣本已於 2026-09-01 由 Nik 完成 12/12 `PASS`，正式結果檔驗證通過。這只代表抽樣 gate，不代表 50 場逐場人工審核；本文件以下內容保留修正當日的歷史狀態。

## 結論

本輪修正已完成程式、資料候選版與 50 場重新抽取，但尚未完成新的真人抽查，因此正式 app DB 仍維持唯讀且不得匯入 v5 DecisionTrace。舊 v4 的 12/12 場人工結果全部為 `NEEDS_CORRECTION`，應保留為問題證據，不得改寫或視為 v5 的人工驗證。

核心產品目標仍是預測每位 voter 的 `FOR`／`AGAINST`、dissent 身分與票數，以及最終政策；已公開的與會名單是輸入，不是預測標籤或成果。

## 已完成修正

1. 投票標籤候選庫已修正已知重大錯誤。新 v5 trace 對 FOMC-2006-09-20 輸出 10–1，對 FOMC-2010-01-26 輸出 9–1。
2. Transcript segmentation v3 修正 inline speaker handoff，候選庫為 `fomc_simulation.transcript_segmentation_v3_candidate.sqlite`，SHA-256 `9be4bcf672b2f1dcf53f31a8fc985fb1acc02e9ed55a0de505bf1d82c7ebbcb3`。
3. 新增 `atomic_one_clause_monitor_v1` 語意 gate：價格指數的百分比主張必須使用 YoY transform；拒絕非負 level 的零門檻、單邊規則冒充區間／對稱目標、跨 series 複合主張，以及單一閾值冒充時間路徑。
4. 舊 v4 離線稽核涵蓋 50 場、51 個 assumptions，發現 28 個無效 assumptions、涉及 27 場；已完成的 12 場真人校準中，9 個人工判為無效者全被 gate 擋下，3 個人工判為有效者全通過。這只證明 12 場校準，不代表其餘 38 場已有人工 ground truth。
5. 新 extractor lineage 為 `codex-subscription-decision-trace-v5-atomic-monitor`，使用 transcript v3 候選庫與 ChatGPT 訂閱執行；Platform API calls = 0、Platform API cost = US$0。

## 新 50 場批次

- 50/50 cases completed
- 55 subscription requests，其中 5 次 semantic repair
- 56 assumptions
- 251 debate items，其中 196 participant-level items
- 1,002 evidence references，其中 517 transcript references
- 7,184,958 input tokens；140,032 cached input tokens
- 113,665 output tokens；28,510 reasoning output tokens
- batch SHA-256：`225022e44b6c2f358c4777b9abcd54abf7b83487a9a6e73de1e050340f0e8936`

確定性 QA 已重新驗證 50/50 場，狀態維持 `PENDING_HUMAN_REVIEW`。QA queue SHA-256 為 `e34719b420fbcc675cab397b0c04fd43dd99dc9ff0e7fbeb5b39d7920bde33b9`；15 場為 HIGH、35 場為 STANDARD。

## 新真人抽查 gate

新的 12 場樣本已從 v5 QA queue 確定性凍結：5 場 semantic repair、兩種 attribution flag representative、5 場 STANDARD。樣本檔案 SHA-256 為 `93a44911021774106cb51a01da01afd798d3c9130ec9758911178df54a9f9436`，payload hash 為 `028c259ed6ad2383e3ce67d38ad8672e3916fda357c834e249c6cbd15eb555ea`。

真人抽查尚未開始。任一 case 為 `FAIL` 或 `NEEDS_CORRECTION`，正式匯入 gate 必須保持 `BLOCKED`；12 場全數 `PASS` 也只代表抽樣 gate，不能宣稱 50 場逐場人工驗證。

## 驗證證據

- `python -m unittest discover -s tests -q`：Ran 236 tests，OK。
- `fred_fomc_real.sqlite`：integrity `ok`、foreign key violations 0、SHA-256 `a7fd78ff8cb52eca2a81ea6b9777bdf048711f2ed6c0a0a660d6d3a777527960`。
- `fomc_simulation.sqlite`：integrity `ok`、foreign key violations 0、SHA-256 `83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c`。
- `fomc_simulation.vote_core_candidate.sqlite`：integrity `ok`、foreign key violations 0、SHA-256 `4bb8f919b7933186986803fb569e8e84e0ab052f6edcf148f5d58e1a959f8212`。
- `fomc_simulation.transcript_segmentation_v3_candidate.sqlite`：integrity `ok`、foreign key violations 0。

## 仍未完成

1. 新 v5 12 場真人抽查與結果 validator。
2. 任何候選資料向正式 app DB 的升級或 DecisionTrace 匯入。
3. v14 manifest 已改為要求 v5 真人 results；在人工 gate 前會因 results 缺失而拒絕建立。8/29 的既有 handoff 是歷史快照，不應覆寫。
4. 最終影片、三次口述 rehearsal、提交確認與第二人 signoff。

Current-code Microsoft Edge 三模式演練已完成：normal、子程序 no-key、stop-and-restart 的四個頁面 body-text 與 screenshot SHA-256 均逐一相等，submission gate 已重算為 PASS。另新增 transcript v3 candidate 的核心逐人投票標籤 gate：166 場、1,736 票、103 筆 dissent、已知 voter roster 與 labels 零落差。目前 12 道 gate 為 9 PASS／3 BLOCKED。

Simulation 不再只顯示 aggregate dissent F1 與原始 JSON；current-code UI 已把 known roster 明示為輸入，逐人列出 predicted／actual `FOR`／`AGAINST` 與 dissent TP／TN／FP／FN。2022-03-15 demo 直接顯示 8/9 並漏判 James Bullard；Anonymous 模式以匿名 ID 呈現。更新後 Edge normal／no-key／restart 再次逐頁驗證完全等價，rehearsal SHA-256 為 `dbfb2ae578f3e0fe46af6f03be904c456bed04a4e36436739c3807db0a7d513b`。
