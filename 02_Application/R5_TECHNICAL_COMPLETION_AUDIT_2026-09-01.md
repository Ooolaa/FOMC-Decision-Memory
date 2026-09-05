# FOMC 決策記憶系統 R5 技術完成稽核

稽核日期：2026-09-01  
規格來源：`FOMC_決策記憶系統_Hackathon_MVP_開發計畫_R5.docx`  
範圍：R5 技術 MVP 與工程移交；依使用者指示，不把影片、三次口述彩排、主辦方確認或第二人投稿簽核列為目前技術完成條件。

## 結論

**R5 technical MVP：READY。**

`python -m decision_memory.submission_gate --scope technical` 回傳 11/11 checks `PASS`、0 blockers、exit code 0。完整 submission gate 仍為 11/12，只缺 `submission_signoff`；這是延後的真人投稿行政工作，不是技術缺陷。

## 逐里程碑證據

| R5 里程碑 | 結論 | 直接證據 |
|---|---|---|
| M0 可重現基礎 | PASS | `decision_memory.preflight`：22 series、166 meetings、1,541,111 snapshots、cutoff violations 0、SQLite integrity `ok`、FK 0 |
| M1 政策利率輸入 | PASS | DFEDTAR／DFEDTARU／DFEDTARL 均存在；24 場 pre-range 與 142 場 range coverage；每場 compact policy context 最多 9 筆，cutoff violations 0 |
| M2 app DB 與企業案例 | PASS | 正式 `fomc_simulation.sqlite` hash 固定、integrity `ok`、FK 0；企業 fixture 為 synthetic／composite，review request／reviewed workflow 由測試覆蓋，正式 DB 預設唯讀 |
| M3 文件、名冊、Outcome、DecisionTrace | PASS | candidate DB：166 場 labels、1,736 筆 voter votes、103 筆 dissent、roster mismatch 0；vote parser：4 份多投票文件、8 rounds、5 meeting mappings、0 errors；DecisionTrace 50/50 deterministic QA 通過 |
| M3 人工抽樣 | PASS（限樣本） | 凍結 12-case 樣本由 Nik 完成 12/12 `PASS`；`formal_import_gate=PASS`；results SHA-256 `449b0bc91bba5b15adf102ad0a2314090a6efdaa880eaf881b9c927de57b7de0`。不得推稱 50 場逐場人工審核 |
| M4 deterministic lag | PASS | `rate_only_response_v1` 重算 55＋15＝70 場約束期，Frozen 45 中 9 場設限；recognition 不因利率約束而設限；非利率工具不關閉事件 |
| M5 reaction 與模擬 | PASS（開發證據） | ordered-logit pooled model 收斂；Hackathon feature contract 明列 BAA10Y 為窄義 proxy；structured simulator、Chair proposal、semantic validation 與票數平衡均有測試 |
| M6 評估與 cache | PASS（single-run development） | 5 個 subscription variants 各 45/45；八列矩陣完整；known voter roster 僅作輸入，逐人 `FOR`／`AGAINST`、dissent TP/TN/FP/FN 才是預測評估；Platform API calls/cost 均為 0 |
| M7 三頁產品 | PASS（技術） | Decision Replay、Assumption Monitor、Simulation & Evidence 已實作；current-code rehearsal 為 3 modes／4 views，畫面與 body text 等價，rehearsal hash `dbfb2ae578f3e0fe46af6f03be904c456bed04a4e36436739c3807db0a7d513b` |
| Immutable build | PASS | v16 portable manifest 收錄 140 個檔案，`root=.`，manifest hash `28ceabd494f88659009c6e4cd7ecd5778ff63c2a7dd0141813a2c05bde7e7706` |

## 驗證輸出

### 完整測試

```text
python -m unittest discover -s tests -p 'test_*.py'
Ran 246 tests in 50.931s
OK
```

### 技術 gate

```text
python -m decision_memory.submission_gate --scope technical
status=READY
check_count=11
pass_count=11
blocker_count=0
exit code=0
```

### 完整投稿 gate

```text
python -m decision_memory.submission_gate
status=BLOCKED
pass_count=11
blockers=[submission_signoff]
exit code=1
```

此 `BLOCKED` 只表示尚未執行真實影片／彩排／主辦方確認／第二人簽核，不應解讀為資料庫、模型、UI 或工程移交失敗。

## 仍需保留的限制

- 12/12 人工通過只涵蓋凍結樣本，不是 50 場逐場人工審核。
- 五種 Frozen 45 結果是單次 ChatGPT 訂閱開發量測，不是 Responses API repeats、信賴區間或正式 Self-Harness promotion 證據。
- BAA10Y 是 Hackathon 核准的窄義信用條件 proxy，不是 NFCI，也不構成 production feature selection 結論。
- 正式 app DB 與 source DB 仍保持唯讀；人工抽樣通過沒有自動匯入候選資料。
- 本成果是離線研究 MVP，不是雲端多人 production 系統。

## 工程師接手判準

工程師應先執行完整測試、`decision_memory.preflight`、technical gate 與 handoff verifier。只有四者均通過，才沿用這份技術快照；任何列入 v16 manifest 的檔案變動都必須建立新 manifest，不得修改既有 v16 內容後仍沿用其 READY 宣稱。
