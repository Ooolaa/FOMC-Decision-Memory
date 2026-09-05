# FOMC 決策記憶 Lab · FOMC Decision Memory Lab

**組織忘記的不是資料，是「當初為什麼這樣決定」。**
這套系統把每一次重大決策存成可追溯的 **DecisionTrace**——當時看見哪些數字、
桌上有哪些選項、誰支持誰反對、哪些假設撐著這個決定——並在第一個反證出現時
把討論重新叫起來。

我們用 **FOMC（聯準會公開市場委員會）** 當 benchmark：它是全世界少數把
「會前資料、會中爭論、逐人投票、事後紀要」全部公開的決策機構，因此每一個
宣稱都可以拿真實紀錄回測，而不是靠 demo 講故事。

<p align="center">
  <img src="docs/screenshots/07-member-detail.png" width="100%"
       alt="FOMC 會議現場：12 位委員的席次、3 位舉手反對，右欄展開 Neel Kashkari 的異議理由與原文引證">
</p>

<p align="center">
  <a href="#60-秒上手">60 秒上手</a> ·
  <a href="#這套系統是怎麼運作的">運作方式</a> ·
  <a href="#專案結構">專案結構</a> ·
  <a href="#團隊">團隊</a>
</p>

---

## 這個專案在解什麼問題

市場每次都在猜同一件事：「這次會不會降息？」但決定利率的不是「市場」，
是桌邊那十幾個人——他們各自投票，而且**會有人投反對**。

沒有人在做的是：說出**哪一位**會反對，以及**為什麼**。

放大來看，這正是所有組織的通病。真正的問題有兩層，而且兩層都是**時間**問題：

| | 定義 | 誰該負責縮短 |
| --- | --- | --- |
| **Recognition lag（認知落後）** | 第一個反證出現後，官方措辭花多久才承認狀態改變 | **這是我們要幫組織縮短的。** |
| **Action lag（行動落後）** | 承認之後，政策花多久才真正改變 | 可能是刻意的執行節奏，不該混稱為組織失憶 |

把兩者混為一談，就會把「刻意等待」誤判成「集體失憶」。本系統把它們分開量測。

**目標使用者**

- 需要追蹤重大決策依據的企業主管與策略團隊
- 必須定期更新投資／經濟觀點的研究與風險團隊
- 想回顧群體決策品質、分歧與反應速度的治理單位

---

## 60 秒上手

### 最快的路：兩個離線網頁，零安裝

不需要 Python、資料庫、網路或 API 金鑰——`git clone` 後用瀏覽器直接開：

```sh
git clone https://github.com/Ooolaa/FOMC-Decision-Memory.git
cd FOMC-Decision-Memory
open FOMC_RAG_Vote_Simulator.html          # macOS
# Windows: start FOMC_RAG_Vote_Simulator.html
# Linux:   xdg-open FOMC_RAG_Vote_Simulator.html
```

| 檔案 | 內容 | 需要什麼 |
| --- | --- | --- |
| **`FOMC_RAG_Vote_Simulator.html`** | **主要展示**：BM25 檢索 244 場會議的 3,553 段原文，推論政策方向、逐一委員投票與出處 | 只要瀏覽器 |
| `05_Design_Canvas/fomc-meeting-scene.html` | 會議現場動畫：12 個席次、舉手反對、點任一位展開理由 | 只要瀏覽器 |

> 頁面內含完整倒排索引，第一次按「執行 RAG 情境預測」約 1 秒建索引，之後即時。
> **整個模擬過程不呼叫任何 LLM**，全部是瀏覽器內的確定性算術——所以離線也跑得動，
> 而且同樣的輸入永遠得到同樣的輸出。

### 完整的路：R5 Streamlit 應用程式

三個離線頁面（Decision Replay、Assumption Monitor、Simulation & Evidence）在
`02_Application/`。**它需要 4 個 `.sqlite` 執行期資料庫，那些檔案刻意不放進版本庫**
（見〈[資料在哪裡](#資料在哪裡)〉）。安裝與啟動步驟完整寫在
**[`README_zh-TW.md`](README_zh-TW.md)**。

```sh
# 需 Python 3.11（不是 3.12，原因見 README_zh-TW.md）
python3.11 -m venv 03_Environment/.venv
03_Environment/.venv/bin/python -m pip install -r 02_Application/requirements.txt

cd 02_Application
export PYTHONUTF8=1
export FOMC_APP_DB="$PWD/fomc_simulation.decision_trace_50_display.sqlite"
../03_Environment/.venv/bin/python -m streamlit run app.py \
  --server.headless true --server.address 127.0.0.1 --server.port 8503
```

---

## 這套系統是怎麼運作的

```
communications.csv          479 份 FOMC 文件（243 會議紀要 + 225 聲明 + 11 已排定）
        │                   雙重 UTF-8 編碼修復 → 解析
        ▼
04_RAG_Vote_Simulator/build_rag_index.py
        │                   切段、標條件標籤、抽異議句、判鷹鴿方向
        ▼
fomc_rag_index.json         244 場會議 · 3,553 段可檢索原文 · 68 位具名委員 · 88 筆異議
        │
        ▼
build_app.py  ──►  FOMC_RAG_Vote_Simulator.html   （索引內嵌，離線可跑）
                          │
                          ├─ BM25 取回 K=400 段 → 去重成會議
                          ├─ 依條件標籤重排（矛盾標籤 δ=1.5 扣分）→ 取前 M=8 場
                          ├─ 類比會議的實際決策加權投票  ──┐
                          ├─ pooled ordered logit（R5 封存係數，唯讀）─┤→ 混合方向
                          └─ 逐人票 = 歷史異議基準率 × 方向張力 × 自身原文相關度
```

從語料萃取出來的東西：

| 項目 | 數量 |
| --- | --- |
| 會議 | **244 場**（2000-02-02 – 2026-07-29） |
| 可檢索段落 | **3,553 段**（單段上限 1,400 字元） |
| 具名投票委員 | **68 位** |
| 異議紀錄 | **88 筆**，含鷹／鴿方向判定 |
| 政策行動標記 | 221 場有明確利率決策（升息 40、維持 147、降息 34） |

**行動標記的驗證**：與 R5 封存的 `pooled_ordered_logit_v1.json` 的 121 場訓練標籤
比對，**120 場一致**。唯一差異是 2020-03 的臨時降息——R5 標為「維持」，本工具依
聲明原文標為「降息」。

---

## 專案結構

```
FOMC-Decision-Memory/
├── README.md                          ← 你在這裡
├── README_zh-TW.md                    R5 交付說明：安裝、啟動、常見問題
├── README_RAG_VOTE_SIMULATOR_zh-TW.md RAG 模擬的完整模型權重、參數與邊界
├── AGENTS.md                          給 AI coding agent 的工作守則
│
├── FOMC_RAG_Vote_Simulator.html       ★ 主要離線展示（開瀏覽器即可）
├── communications.csv                 479 份 FOMC 原始文件語料
│
├── 01_Release-Handoff/                釋出 manifest、SHA-256、IT 部署文件
├── 02_Application/                    R5 應用程式本體（1,005 檔，雜湊列管）
├── 04_RAG_Vote_Simulator/             索引建置與回測評估腳本
├── 05_Design_Canvas/                  會議現場動畫
├── docs/ENGINEERING_LOG_zh-TW.md      問題與解法摘要（開發過程）
└── docs/screenshots/                  README 截圖
```

### 資料在哪裡

4 個執行期 `.sqlite` 資料庫與 `official_documents/` **刻意不在版本庫裡**，以
external frozen inputs 另外遞交。這不是檔案損壞：`RELEASE_MANIFEST.json` 的
`excluded` 明列 `"runtime databases"`，`SOURCE_FILES.txt` 也沒有任何 `.sqlite` 條目。
8 項檔案的 SHA-256 清單在
[`02_Application/IT_DATA_INPUTS_zh-TW.md`](02_Application/IT_DATA_INPUTS_zh-TW.md)。

同樣地，`artifacts/codex_subscription/` 與 `artifacts/llm_preflight/`（385 個封存的
模型 run，共 360 MB）由 payload 自己的 `.gitignore` 排除，改由 artifact manifest
治理。**上面兩個離線網頁不需要其中任何一項。**

---

## 團隊

| GitHub | 角色 |
| --- | --- |
| [@Ooolaa](https://github.com/Ooolaa) | 專案維護 |
| [@b91303046](https://github.com/b91303046) | 共同開發 |
| [@Tempest-s](https://github.com/Tempest-s) | 共同開發 |
| [@phidelia-tsao](https://github.com/phidelia-tsao) | 共同開發 |

詳見 [`CONTRIBUTORS.md`](CONTRIBUTORS.md)。

---

## 資料來源

- **FOMC 會後聲明、會議紀要、逐字稿**：Board of Governors of the Federal Reserve System（公開資料）
- **總體經濟序列**：FRED / ALFRED（嚴格 point-in-time，模型只看得到會議當時已公布的 vintage）

本專案與美國聯準會無任何關聯，所有模擬輸出皆為研究用途，不構成投資建議。
