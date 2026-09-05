# Hackathon Submission Copy

## 你想打造什麼？

我們要打造一套給企業決策者、策略團隊與研究人員使用的「AI 決策記憶系統」。它不是只回答「現在該怎麼做」，而是把每次重大決策保存成可追溯的 DecisionTrace：當時看見哪些資料、有哪些選項與爭論、最後為何做出決定、哪些假設支撐這個決定，以及誰支持或反對。

許多組織真正的問題不是沒有資料，而是數月後已經沒有人能清楚回答：「當初為什麼這樣決定？」當外部訊號開始推翻原有假設時，討論也不一定會及時重啟。本系統會把假設綁定可監控的資料序列；當第一個反證出現時產生可稽核提醒，並保留從 contradiction、review requested 到 reviewed 的完整紀錄。

## 目標使用者

- 需要追蹤重大決策依據的企業主管與策略團隊
- 必須定期更新投資／經濟觀點的研究與風險團隊
- 想回顧群體決策品質、分歧與反應速度的治理單位

## Hackathon 期間開發內容

我們以 FOMC 作為公開且可回測的 benchmark，完成一個離線可展示的 MVP：

1. 建立嚴格 point-in-time 的 FRED/ALFRED 經濟資料庫，確保模型只能看到會議當時已公布的數據與 vintage。
2. 將官方 statement、minutes 與公開 transcript 整理成可驗證的證據層，並把歷史會議抽取成 DecisionTrace。
3. 建立結構化多角色模擬：profiles、openings、policy options、Chair proposal 與 votes；所有生成發言都明確標示為 synthetic，不冒充歷史原話。
4. 實作 naked、具名／匿名 persona、reaction／no-reaction 與 date-only memorization probe，分別評估政策方向、action error、dissent precision／recall／F1；完整矩陣只會在五種變體全部完成後發布。
5. 建立 Assumption Monitor，以預先登記的 deterministic 規則計算 first contradiction、statement phrase flip、observable recognition lag 與 rate-only response lag。
6. 提供三個離線頁面：Decision Replay、Assumption Monitor、Simulation & Evidence；Simulation 直接列出已知 voter roster 上逐人的 predicted／actual `FOR`／`AGAINST`、漏判與誤報異議者，不把已公開名冊當成預測能力。另有一個明確標示為 synthetic/composite 的企業案例，證明 schema 不只適用於央行。

## 核心差異

我們不把另一個 LLM 當唯一裁判。資料 cutoff、證據引用、speaker attribution、政策結果、票數平衡與 lag 都由可重跑的規則與 SQLite artifact 驗證；模型負責生成與權衡，系統負責記憶、邊界與稽核。

這個產品最重要的量化命題，是區分兩段時間：

- **Recognition lag**：第一個反證出現後，官方措辭花多久才承認狀態改變。
- **Action lag**：承認之後，政策花多久才真正改變。

前者才是我們要協助企業縮短的延遲；後者可能是刻意的執行節奏，不能混稱為組織失憶。

模型只是引擎；真正的產品，是讓組織記得自己為什麼做決定，並在那些理由不再成立時，及早重開討論。

## 揭露

- FOMC 是 benchmark，不是最終產品邊界。
- 企業案例是示意用 synthetic/composite，不是真實客戶案例。
- Recognition lag 是可觀察的 statement 文字代理，不是對內部認知的讀心。
- Hackathon reaction model 使用 BAA10Y 作為窄義信用情勢 proxy；它不是 NFCI，也不是完整金融情勢指標，production feature selection 將另行評估。
- 開發期 LLM 批次使用 ChatGPT 訂閱，不產生 Platform API 費用；正式上線仍規劃使用 API。
- 五種變體均已完成 45/45，八列矩陣已通過 run hash 與 aggregate 重算；開發批次未改走 Platform API。Demo 案例政策方向雖正確，逐人投票只對 8/9，並漏掉實際異議者 James Bullard；此失敗會直接顯示，不以名冊 coverage 或整體政策準確率掩蓋。
- 未完成 repeats、信賴區間或 promotion gate 前，不宣稱模型已具統計顯著優勢。
