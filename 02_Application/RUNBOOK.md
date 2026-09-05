# FOMC Decision Memory Lab：Hackathon R5 操作手冊

本專案是一套離線可展示的「聯準會決策預測與證據系統」MVP，以 FOMC 公開資料進行嚴格時點回測、下次會議預測與委員投票證據展示。

## 1. 最快啟動方式

在專案根目錄開啟 PowerShell：

```powershell
.\run_app.ps1
```

預設網址為 `http://localhost:8503`。若需改用其他埠：

```powershell
.\run_app.ps1 -Port 8510
```

也可直接執行：

```powershell
python -m streamlit run app.py
```

核心預測、委員歷史投票與公開發言不需要 `OPENAI_API_KEY`，也不會在頁面載入時
呼叫外部模型或 FRED。只有使用者按下「用 AI 統整預測理由（會呼叫 API）」時，
才會透過 Responses API 傳送畫面已揭露的公開證據摘要。金鑰只允許來自 Windows
User-scope `OPENAI_API_KEY`；不得放入 repo、`.env` 或 Streamlit secrets。AI 統整
預設模型為 `gpt-5.6-terra`，可用非機密環境變數 `FOMC_AI_EXPLAIN_MODEL` 覆寫。
若 API 失敗，UI 會顯示 `AI_AUTH`、`AI_QUOTA`、`AI_RATE_LIMIT`、`AI_NETWORK`
等安全診斷碼，不顯示金鑰或原始請求。Launcher 只會在 Windows User scope 沒有
代理設定時，移除工具執行環境注入的 loopback proxy；不會覆蓋真實使用者代理。

預設啟動會把衍生 `fomc_simulation.decision_trace_50_display.sqlite` 當唯讀展示庫。FOMC-only 介面不提供任何資料庫寫入操作。

## 2. 三個展示頁面

1. **下次會議預測**：以會前資料預測政策方向與投票結構；可逐位查看歷史投票、截止日前官方發言與規則式關注議題推定。
2. **決策重播**：重播 166 場 FOMC 的政策、投票與當時可見經濟資料；51 場另有完整 DecisionTrace。
3. **歷史測試結果**：用白話比較首頁四個模型在 45 場歷史會議的政策方向與反對票表現，並揭露答案記憶風險。

任何 LLM 發言都必須標示為 synthetic，不能當成歷史原話。

## 3. 核心資料與邊界

| 路徑 | 用途 | 規則 |
|---|---|---|
| `fred_fomc_real.sqlite` | 22 個 FRED series、166 場會議、嚴格時點 snapshots | 正式來源庫，唯讀 |
| `fomc_simulation.sqlite` | 文件、roster、舊版 votes、outcomes、DecisionTrace、workflow | UI 讀取；人工 gate 前保留既有 hash，不得誤當成本輪修正版逐人投票標籤 |
| `fomc_simulation.transcript_segmentation_v3_candidate.sqlite` | transcript v3、166 場 outcome、1,736 筆逐人投票、103 筆 dissent，以及已解析公開發言 | v5 人工 QA truth；UI 僅唯讀使用 `public_communication` 作委員證據，其他候選表通過後才可另行核准正式匯入 |
| `fomc_simulation.decision_trace_50_display.sqlite` | transcript v3 候選庫加上已驗證的 50 場 batch DecisionTrace；合計 51 場完整 FOMC replay | Streamlit 預設唯讀展示庫；不得當作模型 Case input 或直接覆蓋正式庫 |
| `official_documents/` | statement、minutes、官方 transcript PDF | 內容以 SHA-256 驗證 |
| `document_manifests/` | 來源 URL、時間、usage class、hash | append-only |
| `metric_spec/` | recognition/action/response lag 的凍結規則 | 禁止賽後調參 |
| `artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/` | 50 場 DecisionTrace 抽取與 QA | transcript v3 + atomic monitor 開發證據；凍結的 12-case 樣本已由 Nik 完成 12/12 PASS，僅代表樣本 gate |
| `artifacts/codex_subscription/r5_variants_v2/` | 五種 R5 變體 | ChatGPT 訂閱執行，Platform API 成本固定為 0 |
| `artifacts/evaluation/` | deterministic baselines、比較矩陣、alert audit | 展示只讀 |

下次會議頁另使用
`fixtures/next_meeting_official_context_2026-09-01.json`：投票者是聯準會官方公布的
2026 年 12 位 FOMC 委員，不是沿用上一場出席名單；四個原 speech ingester 未涵蓋的
地區銀行來源以明確標示的 `source_summary` 補充，原文仍由官方連結查閱。正式來源庫已於
2026-09-01 以 strict point-in-time 模式更新，最新 observation／realtime date 為
2026-08-31；更新前後 1,541,111 筆 meeting snapshot 的數值差異為 0，稽核證據在
`artifacts/evaluation/source_refresh_2026-09-01_audit.json`。

正式 Frozen manifest：

```text
document_manifests/current_45_as_of_2026-08-27_source_a7fd.json
```

## 4. 資料與程式驗證

全套測試：

```powershell
python -m unittest discover -s tests
```

來源庫 preflight：

```powershell
python -m decision_memory.preflight --database fred_fomc_real.sqlite
```

多投票原文與 Frozen rate-constraint audits：

```powershell
python -m decision_memory.votes
python -m decision_memory.lag_spec
```

前者必須得到 4 份雙投票文件、8 個 blocks、5 個 meeting mappings 與 0 errors；
後者必須得到 55+15=70 場約束期及 Frozen 9/45 constrained cases。兩個 artifact
皆採 immutable write，內容不同時拒絕覆寫。

SQLite 必須同時滿足：

- `PRAGMA integrity_check = ok`
- `PRAGMA foreign_key_check` 回傳 0 列
- meeting snapshot 不得出現 cutoff 後資料
- 每場 policy-rate context 最多 9 筆，包含當前 regime duration 與最近 8 次變動

## 5. DecisionTrace 批次

正式開發批次使用 ChatGPT 訂閱登入，子程序會移除 API key 相關環境變數：

```powershell
python -m decision_memory.decision_trace_subscription `
  --source fred_fomc_real.sqlite `
  --app fomc_simulation.transcript_segmentation_v3_candidate.sqlite `
  --output-directory artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3 `
  --max-new-cases 50 `
  --confirmation RUN_CODEX_SUBSCRIPTION_DATA_PROCESSING
```

批次可續跑；既有 artifact 必須通過 bundle hash、run hash、model 與 extractor version 驗證才可重用。

完成後執行獨立 QA：

```powershell
python -m decision_memory.decision_trace_qa `
  artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/batch_status.json `
  --source fred_fomc_real.sqlite `
  --app fomc_simulation.transcript_segmentation_v3_candidate.sqlite `
  --output artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/qa_queue.json
```

QA 會重新驗證 schema、文件 locator/excerpt、transcript speaker attribution、政策 outcome、投票、SQLite integrity/FK 與資料庫 hash。輸出 `qa_queue.json` 本身維持 `PENDING_HUMAN_REVIEW`，人工判定另存於 `human_review_results_v1.json`；兩者不得互相覆寫，也不得自動匯入正式 app DB。

凍結不可事後挑選的 12 場人工抽查樣本：

```powershell
python -m decision_memory.human_review_sample `
  --qa-queue artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/qa_queue.json `
  --target-count 12 `
  --output artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json
```

選樣包含全部 5 場 semantic-repair cases、兩種 attribution QA flag representative，以及 5 場 STANDARD cases。manifest 保持唯讀，人工結果另存；完整步驟見 `DECISION_TRACE_HUMAN_REVIEW.md`。2026-09-01 已由 Nik 完成 12/12 `PASS`，formal import gate 為 `PASS`；這只代表不可事後挑選的 12-case 樣本通過，仍不得宣稱 50 場逐場人工審核，正式 DB 也未因此自動改寫。

人工 QA 不評「是否猜中與會名單」：voter roster 是已知輸入。`participant_attribution_supported` 只保護歷史發言資料品質；核心預測評估使用已知 voter 集合上的逐人 `FOR`／`AGAINST`、dissent F1 與最終政策。v5 的 `decision_and_vote_match_labels` 應對照 transcript v3 candidate，不得回頭使用尚未升級的正式 app DB。

真人完成結果檔後，必須明確指定 v5 sample 與 results 執行 `python -m decision_memory.human_review_results --sample <v5 sample> --results <v5 結果檔>`；sample hash、案例集合、attestation、時間、checklist 或 decision 任一不符都 fail closed。這個 validator 只驗證人工結果契約，不會自動匯入 DB。

## 6. R5 五種模型變體

五種變體固定為：

- `naked_frozen_llm`
- `named_persona_reaction`
- `anonymous_persona_reaction`
- `named_persona_no_reaction`
- `date_only_memorization_probe`

Reaction feature contract 固定於 `model_spec/reaction_feature_contract_hackathon_r5_v1.json`：Hackathon R5 使用 BAA10Y 作為窄義信用情勢 proxy，明示不是 NFCI；此核准沒有改變 v1 模型或既有 bundle hash，不觸發重跑。production 不得沿用此核准冒充已完成 NFCI 比較。

先做無模型 preflight：

```powershell
python -m decision_memory.subscription_variant_runner `
  --variant-id named_persona_reaction `
  --preflight-only
```

再以訂閱額度執行；每個 case 的五個 stage 必須連續，不跨 case 交錯：

```powershell
python -m decision_memory.subscription_variant_runner `
  --variant-id named_persona_reaction `
  --max-new-cases 45 `
  --confirmation RUN_CODEX_SUBSCRIPTION_DATA_PROCESSING
```

階段與 reasoning effort：

| Stage | Effort |
|---|---|
| profiles | medium |
| openings | high |
| options | high |
| chair | high |
| votes | medium |

只有 Chair 可以選定 final proposal；votes 必須每位 participant 恰一票。Schema/refusal/incomplete 直接失敗；只有語意違規可帶具體違規項 repair 一次。

匿名變體在呼叫模型前會搜尋序列化 bundle，只要仍含真實姓名或 participant ID 就 fail closed。評估前才在模型輸入之外恢復真實 ID。

五列全部完成後產出八列矩陣（三個 deterministic baseline 加五個 LLM variant）：

```powershell
python -m decision_memory.variant_matrix
```

## 7. Recognition 與 response 指標

`inflation_transitory_v1` 的凍結示例：

| 事件 | 日期／數值 |
|---|---:|
| first contradiction | 2021-05-12 |
| statement phrase flip | 2021-12-15 |
| rate-only policy response | 2022-03-16 |
| observable recognition lag | 217 天 |
| action lag | 91 天 |
| response lag | 308 天 |

解讀限制：

- recognition lag 是可觀察 statement 措辭代理，不是讀心。
- action lag 是政策節奏，不等同組織失憶。
- QE、Operation Twist、taper 與 forward guidance 不在 hackathon 的 rate-only action 定義內。
- 利率約束期固定為 `2009-01-27..2015-10-27`（55 場）及 `2020-04-28..2022-01-25`（15 場），checksum 為 70 場。
- action/response 的 censoring 是 informative，只能當描述性統計；旗艦 recognition lag 不受 ZLB 設限。

## 8. 現況、限額中斷與續跑

截至 2026-09-01：

- 50/50 場 DecisionTrace 已完成抽取與確定性重驗；凍結的 12-case 樣本由 Nik 完成 12/12 `PASS`，`human_review_results_v1.json` 驗證通過。不可把抽樣通過說成 50 場逐場人工審核，亦未自動寫入正式 app DB。
- 50 場 batch 已物化至獨立展示庫；加上既有 2022-03-15 人工 golden trace，決策重播分為 51 場完整 DecisionTrace 與 115 場政策／投票／經濟基礎案例。正式 app DB 仍未修改。
- `date_only_memorization_probe`、`naked_frozen_llm`、`named_persona_no_reaction`、`named_persona_reaction`、`anonymous_persona_reaction` 均為 45/45 完成。
- `anonymous_persona_reaction` 只從 32/45 checkpoint 續跑剩餘 13 場；完整批次共 226 個 subscription requests、1 次 repair、`platform_api_calls=0`、`platform_api_cost_usd=0`。
- 45 份 run artifact 的 SHA-256 全部與狀態檔一致，完整聚合也已獨立重算一致。
- 正式八列矩陣已產出為 `artifacts/evaluation/r5_subscription_variant_matrix_v1.json`，SHA-256 為 `d3ad48b9f7fa17320c46e36447ba15b188e3d4320f589b3aaedd5fcc79c3cea0`。現行程式的 Edge 三模式演練與 166 場動態案例選單已通過；v28 可攜式不可變清單綁定聯準會專用程式、下一場四模型鎖定預測、人工結果、9/1 來源庫、決策脈絡展示庫與介面證據，移交後不依賴原電腦絕對路徑。
- Simulation 的主表使用會前已知 voter roster 對齊每位 predicted／actual `FOR`／`AGAINST`，並直接列出 dissent 的 TP／TN／FP／FN、漏判與誤報；roster coverage 不計入預測成績。Named demo 顯示 2022-03-15 為 8/9 並漏判 James Bullard，Anonymous ablation 只顯示匿名 ID。
- date-only policy accuracy 為 91.1%，必須當成記憶化風險警訊，不得當成可部署能力。
- 訂閱開發 runs 不是 Responses API promotion runs，沒有 repeats、CI 或抽樣變異估計。
- 不得宣稱完成 Self-Harness promotion、API-equivalent Frozen promotion 或統計上有效的 persona 改善，除非對應 gate 真正完成。

若需從相同 checkpoint 重現續跑，只能補剩餘場次，且同一時間只能有一個 runner：

```powershell
python -m decision_memory.subscription_variant_runner `
  --variant-id anonymous_persona_reaction `
  --max-new-cases 13 `
  --confirmation RUN_CODEX_SUBSCRIPTION_DATA_PROCESSING
```

完成後依序執行：

```powershell
python -m decision_memory.variant_matrix
python -m unittest discover -s tests
# 重新執行 normal／no-key／restart 的 current-code Edge rehearsal，
# 並建立 artifacts/rehearsal/ui_rehearsal_r5_final_v7.json
python -m decision_memory.artifact_manifest
```

若第一個指令未得到 45/45 `COMPLETED`，停止後續步驟；不得手動拼接比較矩陣。
最終 UI rehearsal 應從
`submission_templates/hackathon_r5_final_ui_rehearsal_v7.json` 複製填寫；投稿檢查
會直接重算 app、launcher、matrix、三份 capture report 與十二張 mode screenshots 的
SHA-256，並要求三種模式的四個 body-text／畫面逐一相等。只有建立一個名為 PASS
的 JSON 不會通過。
v28 不可變清單會從最終演練 JSON 自動展開三份擷取報告、各報告
引用的 mode screenshots 與四張 canonical screenshots；任何超出 workspace root 的
引用都會 fail closed，避免交件壓縮檔只收錄索引而漏掉實際畫面證據。

## 9. 技術交付與之後的正式投稿

目前工程交付先跑技術 gate；它只排除真人投稿簽核，不排除資料、模型、人工抽樣、UI rehearsal 或 immutable manifest：

```powershell
python -m decision_memory.submission_gate --scope technical
```

只有輸出 `status=READY` 且 exit code 0，才可宣稱「R5 technical MVP READY」。

以下展示／投稿行政工作依使用者指示暫緩，真正準備送件時再做：

1. 啟動 UI，確認三頁皆可離線開啟。
2. 預設正式 app DB 啟動時，確認 review 按鈕不可見；需要操作時只用明確指定的副本。
3. 斷網或不提供 API key，再走一次主要展示路徑。
4. 停止服務後確認 health 失敗，再用 `run_app.ps1` 重啟並確認 health 回復。
5. 重新產出且驗證 immutable artifact manifest。
6. 錄製並看完 90 秒影片。
7. 由 presenter 完成三次計時四分鐘 rehearsal。
8. 由第二人核對公開連結、聯絡資料、影片與最終提交文字。

完成影片、三次口述、archive、主辦方提交與第二人覆核後，將
`submission_templates/hackathon_r5_submission_signoff_v1.json` 複製為
`artifacts/submission/submission_signoff_v1.json` 並填入真實資料。最後執行：

```powershell
python -m decision_memory.submission_gate
```

只有完整 submission gate 輸出 `status=READY` 且 exit code 0，才可宣稱正式投稿完成。目前只有 `submission_signoff` 尚未執行；這不影響 technical MVP 或工程移交就緒狀態。

產品結語：模型只是引擎；真正的產品，是讓組織記得自己為什麼做決定，並在那些理由不再成立時，及早重開討論。
