# Hackathon R5 Submission Checklist

## 已由自動化驗證完成

- [x] `fred_fomc_real.sqlite`：22 series、166 meetings、strict PIT、cutoff violations = 0。
- [x] 24 場 pre-range 使用 `DFEDTAR`；142 場 range regime 使用 `DFEDTARL/U`。
- [x] 一般 snapshot 上限 6,816；政策利率 context 上限 9 且含 regime duration。
- [x] 380 份官方文件 hash 固定：330 份 statement/minutes，加上跨 2006–2020 的 50 場 transcript sample（11,876 segments）。
- [x] 正式 app DB 在人工 gate 前保持既有 hash；修正版 transcript v3 candidate 已有 166 場 outcome、1,736 筆逐人投票、103 筆 dissent，且 166 場的已知 voter roster 與 vote labels 零落差。roster 是會前已知輸入，不是預測目標。
- [x] 多投票原文 gate：120 份 training minutes、4 份雙投票文件、8 個 vote blocks、5 個明確 meeting mappings、121 場政策票、1,219 票，parse errors = 0；證據為 `vote_parser_audit_v1.json`。
- [x] 50/50 場 FOMC DecisionTrace 已完成抽取與確定性 QA；12-case 凍結樣本已由 Nik 完成 12/12 `PASS`。`qa_queue.json` 與人工 results 分開保存，另有一場完整 enterprise synthetic/composite trace，且未自動寫入正式 app DB。
- [x] v5 DecisionTrace 人工抽查 manifest 已不可變凍結為 12 場：包含全部 5 場 semantic repair、兩種 attribution QA flag representative 與 5 場 STANDARD；選樣 payload hash 為 `028c259ed6ad2383e3ce67d38ad8672e3916fda357c834e249c6cbd15eb555ea`。
- [x] 人工抽查 results validator 已 fail closed：完整 12 場、sample hash、真人 attestation、含時區時間、checklist 與 decisions 全部符合才輸出 `formal_import_gate=PASS`；validator 不會代簽或自動入庫。
- [x] R5 submission gate 已切到 v5 lineage並新增核心逐人投票 candidate gate；技術範圍 11/11 通過。完整 12 道檢查只剩真人 `submission_signoff`，依使用者指示暫緩。
- [x] Final UI rehearsal validator 已鎖定 Edge 1440×1100、app／launcher／matrix hash、normal／no-key／restart capture reports、三模式四畫面 body-text 與 screenshot SHA-256 完全一致。
- [x] observable lag golden path = 217／91／308 天；165 份 statement 規則稽核與 false-flip regression test 通過，並揭露反證前分母只有 1、共存樣本為 0。
- [x] `rate_only_censoring_audit_v1.json` 機械重現 55+15=70 場約束期，以及 Frozen split 的 9/45 rate-constrained cases；recognition 不受設限、action/response censoring 可能 informative。
- [x] enterprise synthetic/composite Context／Options／Debate／Decision／Vote 與 request/reviewed workflow 可持久化。
- [x] pooled ordered logit、16 張實際 roster profile cards、Frozen 45 deterministic baselines、成功/失敗揭露；明示沒有個人係數模型。使用者已核准 Hackathon R5 以 BAA10Y 作為窄義信用情勢 proxy，並明示不是 NFCI。
- [x] 五階段 `gpt-5.6-terra` runner、common schema、sequential cache affinity、semantic repair once、hard cap。
- [x] 5 文件 dry-run：6,816 rows；最新估算 US$0.8995–2.7333，明列非實際 usage。
- [x] 真實 `gpt-5.6-terra` 五文件 sample：US$5 hard cap、實際 token 計價 US$0.99421、五階段一次完成、cache gate 通過。
- [x] R5 五種 subscription ablation/date probe 均完成 45/45；`anonymous_persona_reaction` 從 32/45 checkpoint 續跑剩餘 13 場後完成，總計 226 requests、1 次 repair、Platform API 0 次／US$0。
- [x] 三頁 Streamlit AppTest、server health 200/ok、一鍵 `run_app.ps1`。
- [x] Decision Replay／Simulation 畫面可顯示 source DB SHA-256、outcome evidence SHA-256 與所選 Case bundle SHA-256；不是只顯示人類可讀 ID。
- [x] Simulation 核心表把 known voter roster 明標為會前輸入，逐人顯示 predicted／actual `FOR`／`AGAINST`、正誤、dissent TP／TN／FP／FN 與漏判／誤報名單；Anonymous 模式不顯示真名。2022-03-15 demo 誠實顯示 8/9、漏判 James Bullard。
- [x] `run_app.ps1` 預設保護正式 app DB；未明確指定副本與 `-EnableReviewWrites` 時不顯示 review 寫入按鈕，對正式 DB 啟用 writes 會 fail closed。
- [x] Luna remediation 重測：US$1 hard cap、五階段無 repair、實際成本 US$0.17550248；政策／投票指標與 Terra 相同，cache gate 未通過，明示非受控 model-only comparison。
- [x] 三場 Terra–Luna 受控比較：相同新版 harness 與逐 case 相同 bundle，HIKE／HOLD／CUT 均判對；Terra／Luna dissent F1 分別為 0.667／0.250，五個新增 runs 共 US$8.86148352，低於 US$14 總硬上限；明示非 Frozen promotion。
- [x] 三場 Terra–Luna conditional votes-only 比較：鎖定相同 Terra profiles／discussion／Chair proposal，Terra／Luna dissent F1 分別為 0.667／0.333；六筆共 US$3.89411955，低於獨立 US$6.50 總硬上限、無 repair，且明示非原始 prompt replay／非 Frozen promotion。
- [x] Frozen 45 ChatGPT 訂閱完整變體：naked policy accuracy 97.8%、dissent F1 0.107；named/no-reaction 93.3%、0.286；named/reaction 97.8%、0.311；date-only policy accuracy 91.1% 並列為記憶化警訊。所有 subscription runs 為 Platform API 0 次／US$0，且明示非 API promotion 證據。
- [x] 第五變體 45/45 的 run SHA-256 與聚合已獨立重算一致；完整八列矩陣已建立，SHA-256 為 `d3ad48b9f7fa17320c46e36447ba15b188e3d4320f589b3aaedd5fcc79c3cea0`。
- [x] 完整回歸與四個 SQLite integrity/FK 通過；source/formal app SHA-256 仍與既有基線相同。
- [x] v16 portable offline build artifact manifest：綁定 current-code UI rehearsal、v5 真人 results、評估矩陣與最新技術 gate；`root=.`，移交後不依賴原電腦絕對路徑。任何列入檔案改動後都必須重建並重新驗證。

## Current-code Edge 三模式演練

- [x] Microsoft Edge 152.0.4191.53、1440×1100 已逐頁重跑三頁 FOMC view 與企業 Decision Replay。
- [x] No-key rehearsal：只從子程序移除 `OPENAI_API_KEY`，四頁仍完全依 frozen artifacts 渲染。
- [x] Restart rehearsal：停止後 health probe 確實失敗，再以 `./run_app.ps1` 恢復至 `status=200 body=ok`。
- [x] 新增逐人投票核心表後重新執行三模式；四頁 body-text 與 screenshot SHA-256 逐一完全相等。證據 `artifacts/rehearsal/ui_rehearsal_r5_final_v1.json` SHA-256 為 `dbfb2ae578f3e0fe46af6f03be904c456bed04a4e36436739c3807db0a7d513b`。
- [x] 企業 review 按鈕已在 app DB 複本完成 `CONTRADICTION → REVIEW_REQUESTED → REVIEWED`，正式 DB hash 保持不變。

## 需要出資者／使用者決定

- [x] 指定 `gpt-5.6-terra` 五文件 sample 的 self-funded hard cap為 US$5。
- [ ] 確認 hackathon credits 是否可用；若不能確認，按 self-funded cap 計。
- [x] 只執行一次成功 sample，保存 actual input/cache-write/cached/output/reasoning tokens、成本、延遲。
- [x] 開發資料處理全部改用 ChatGPT 訂閱登入；正式上線仍保留 Responses API runner，正式 API 成本待上線前重新校準。
- [x] 已決定以 ChatGPT 訂閱執行 naked LLM、named/anonymous、reaction/no-reaction 與 date-only probe；開發期間不使用 Platform API。
- [x] 50 場 DecisionTrace 已用 ChatGPT 訂閱完成抽取；在人工 QA 通過前，不得由確定性重驗推稱已全部人工審核。
- [x] reaction feature contract 已凍結：Hackathon R5 接受 BAA10Y 窄義信用情勢 proxy；契約不改模型權重／bundle hash，因此既有 reaction-dependent variants 不需重跑。正式 production 仍須另行比較 NFCI。

## 已完成的人工技術 QA

- [x] 真人 Nik 依 `DECISION_TRACE_HUMAN_REVIEW.md` 完成 12/12 場抽查並另存 results；sample manifest 未修改，結果 validator 為 `PASS`。

## 正式投稿時才執行（目前依使用者指示暫緩）
- [ ] 口述計時 rehearsal：4 分鐘內照 `DEMO_SCRIPT.md` 完整走完（瀏覽器操作路徑已驗證）。
- [ ] 錄製並觀看 90 秒備援影片；確認字體、聲音、敏感資訊與 synthetic 標示。
- [x] 最終 submission 文案已寫入 `HACKATHON_SUBMISSION.md`，明列目標使用者、問題、hackathon build、資料邊界與目前失敗。
- [ ] 將提交副本的 artifact manifest、source/app hash 與影片檔名寫入最終交件紀錄。
- [ ] 依 `submission_templates/hackathon_r5_submission_signoff_v1.json` 建立真實 signoff，並確認 `python -m decision_memory.submission_gate` 回傳 `READY`／exit code 0。
- [x] 只續跑 `anonymous_persona_reaction` 剩餘 13 場並完成 45/45；八列矩陣、最終截圖與 v16 manifest 均已納入技術 freeze。

## Fail-closed 最終檢查

- [ ] 不存在未標示的 synthetic quote。
- [ ] 不存在本場 statement/minutes/outcome/votes 進入 Case input。
- [ ] 不以 `gpt-5.6-terra` 以外模型靜默替代。
- [ ] 不以 dry-run 估算冒充實際 API usage。
- [ ] 不以 policy-only baseline 宣稱已有 dissent F1。
- [ ] 不把 observable recognition proxy 描述成內在認知事實。
- [ ] 不把 informative-censored action/response 統計描述為無偏估計。
