# FOMC Decision Memory Lab — 4 分鐘 Demo Script

## 0:00–0:35｜產品問題

開場畫面：**Assumption Monitor → 企業示意案例**。

> 很多重大決策失敗，不是因為當時沒有資料，而是組織忘了當初依賴哪些假設，也沒有在假設失效時主動重開討論。這是一個決策記憶系統；FOMC 是可公開查核的 benchmark，畫面上的企業案例是 composite 示意，不是真實客戶。

指出 BAA10Y 假設、首次反證與「送出審視」按鈕。只有使用
`run_app.ps1 -AppDatabase <副本> -EnableReviewWrites` 啟動時才會出現按鈕；正式
app DB 預設唯讀且 launcher 會拒絕開啟 writes。

## 0:35–1:25｜Decision Replay

切到 **Decision Replay**。

> 系統重播 2022 年 3 月會議。經濟資料只取會議前一日 cutoff 當時可見的 vintage；本場 statement、minutes、結果與票數只作會後 label，不能回灌模型。

依序指出：

1. cutoff = 2022-03-14。
2. 實際決策 HIKE、目標區間 0.25–0.50%。
3. human-audited DecisionTrace 的 options、committee-level debate、decision 與 8–1 投票。
4. 所有 excerpt 都能回到本機 hash 固定的 Federal Reserve 官方 HTML；minutes 沒有人名歸屬時不捏造 participant quote。

## 1:25–2:25｜旗艦量化證據

切到 **Assumption Monitor → FOMC**。

> 我們不讓另一個 LLM 判斷 Fed 何時「真正想通」。recognition 是預註冊 statement phrase-set 的可觀察代理，action 是政策利率的機械變動。

指出三個數字：

- 2021-05-12 首次 CPI 反證。
- 2021-12-15 statement 措辭翻轉，observable recognition lag = 217 天。
- 2022-03-16 升息，action lag = 91 天；response lag = 308 天。

主動揭露：

> 217 天不是讀心；91 天可能是刻意政策節奏，不宣稱是失憶。零利率約束只影響 rate-only action/response，不影響 statement recognition。

指出 `Frozen rate-constrained = 9 / 45`，並說明該數字由凍結 meeting split 與
`rate_only_response_v1` 的兩段日期邊界機械重算，不是人工挑選。

## 2:25–3:30｜Simulation & Evidence

切到 **Simulation & Evidence**。

> 系統已把 profiles、openings、options、Chair proposal、votes 做成五階段 Structured Outputs runner；同一 case 連續執行、共享 stable prefix，並記錄 cached tokens、成本與延遲。只有語意違規 repair 一次；schema/refusal 直接失敗。

指出 Frozen 45 baseline 表與 participant profile card；說明個人欄位只使用會前投票／異議歷史，總體係數仍是 pooled model，沒有虛構個人係數。接著明講目前可比較的單次開發結果：

先停在「Core prediction: per-known-voter FOR / AGAINST」表：

> 每場誰有投票權是會前已知輸入，不是預測成績。系統真正預測的是這 9 位 voter 各自會投 FOR 或 AGAINST。這場政策方向猜對，但逐人只對 8／9；模型預測 0 位異議，實際有 1 位，漏掉 James Bullard。這就是我們把 dissent precision、recall、F1 放在核心，而不讓一致票 accuracy 掩蓋失敗的原因。

> Hackathon feature contract 使用 BAA10Y 作為窄義信用情勢 proxy；它不是 NFCI，也不是完整金融情勢指標。這個選擇只適用比賽 lineage，production 會另外比較。

> persistence baseline 的政策準確率是 82.2%；date-only probe 是 91.1%，這反而提醒我們模型可能記住日期。完整五階段結果中，naked 的 dissent F1 只有 0.107；named/no-reaction 是 0.286；named/reaction 是 0.311。這些都是單次 ChatGPT 訂閱開發測量，不是有 repeats 與信賴區間的 promotion 結論。

指出黃色揭露框並主動說明：

> 五個變體都已完成 45／45，八列矩陣也已由 run hash 與 aggregate 重算驗證。這些開發 runs 都是 Platform API 0 次、US$0；但仍只有單次測量，沒有 repeats 或信賴區間，所以不能宣稱已通過 production promotion gate。正式上線才會使用 API。

## 3:30–4:00｜結尾

回到企業示意案例。

> 模型只是引擎；真正的產品是讓組織記得自己為什麼做決定，並在那些理由不再成立時，及早重開討論。FOMC benchmark 證明每個時間點、規則與錯誤都能被外部查核；同一個 DecisionTrace 與 assumption workflow 可以換成企業自己的資料 adapter。

## 90 秒備援影片 shot list

1. 0–15 秒：產品命題＋企業 composite 標示。
2. 15–40 秒：Decision Replay cutoff、HIKE、官方證據。
3. 40–62 秒：217／91／308 timeline 與 proxy disclosure。
4. 62–82 秒：Frozen baseline、HOLD 錯誤、synthetic/offline 標示。
5. 82–90 秒：結尾句與 repository/RUNBOOK 畫面。

錄影前關閉通知、固定 1920×1080、瀏覽器縮放 100%，全程不要輸入或顯示任何 API key。

## 評審常見追問

**你怎麼知道 Fed 何時重新討論？**  
不知道內在認知；主指標是預註冊 statement phrase flip 的 observable proxy。企業 workflow 才使用真實 `reviewed_at` 使用者事件。

**模型是不是背答案？**  
Case 排除本場 statement/minutes/outcome/votes；date-only memorization probe 的政策準確率仍達 91.1%，因此我們把它揭露為記憶化警訊，而不是把高分當成果。

**為什麼不用 LLM 評 LLM？**  
政策、票數、dissent、lag、evidence ID、cutoff 與 schema 都是 deterministic evaluator；LLM 不作唯一裁判。

**目前最弱的是什麼？**  
五個 Frozen 45 變體與八列矩陣都已完成，但 50 場 v5 DecisionTrace 仍只有確定性重驗，新的 12 場樣本尚待真人 QA。即使完整變體政策準確率高，dissent F1 最高也只有 0.311，且沒有 repeats／CI；這正是目前核心投票預測仍未達 production gate 的限制。
