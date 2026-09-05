# 決策記憶 Lab · Decision Memory Lab

**組織忘記的不是資料，是「當初為什麼這樣決定」。**
這套系統把每一次重大決策存成可追溯的 **DecisionTrace**——當時看見哪些數字、
桌上有哪些選項、誰支持誰反對、哪些假設撐著這個決定——並在第一個反證出現時
把討論重新叫起來。

> **FOMC 不是我們的題目，是我們的驗證場。**
> 我們要解的是任何組織都會發生的決策失憶。但「我們的方法有效」這種話，
> 必須有人能拿真實紀錄反駁才算數——而全世界少數把
> 「會前資料、會中爭論、逐人投票、事後紀要」全部公開的決策機構，就是
> **FOMC（聯準會公開市場委員會）**。所以我們拿它當 benchmark：
> 每一個宣稱都可以回測，而不是靠 demo 講故事。
> 換成董事會、投資委員會或任何有紀錄的決策場域，方法本身不變。

<p align="center">
  <img src="docs/screenshots/07-member-detail.png" width="100%"
       alt="FOMC 會議現場：12 位委員的席次、3 位舉手反對，右欄展開 Neel Kashkari 的異議理由與原文引證">
</p>

---

## 這個專案在解什麼問題

任何一個有紀錄的組織，決策品質都卡在同一件事上：**當初的理由沒有被存下來，
所以反證出現時沒有人被叫醒。** 真正的問題有兩層，而且兩層都是**時間**問題：

| | 定義 | 誰該負責縮短 |
| --- | --- | --- |
| **Recognition lag（認知落後）** | 第一個反證出現後，官方措辭花多久才承認狀態改變 | **這是我們要幫組織縮短的。** |
| **Action lag（行動落後）** | 承認之後，政策花多久才真正改變 | 可能是刻意的執行節奏，不該混稱為組織失憶 |

把兩者混為一談，就會把「刻意等待」誤判成「集體失憶」。本系統把它們分開量測。

放到 FOMC 這個驗證場上，同一個問題長這樣：市場每次都在猜「這次會不會降息？」
但決定利率的不是「市場」，是桌邊那十幾個人——他們各自投票，而且**會有人投反對**。
沒有人在做的是：說出**哪一位**會反對，以及**為什麼**。
能不能說對「誰反對」，就是我們檢驗自己的那把尺——因為方向只有三種、猜得中很容易，
逐人異議猜不中就是猜不中。

**目標使用者**

- 需要追蹤重大決策依據的企業主管與策略團隊
- 必須定期更新投資／經濟觀點的研究與風險團隊
- 想回顧群體決策品質、分歧與反應速度的治理單位

---

## 兩個入口

| | 內容 | 需要什麼 |
| --- | --- | --- |
| **`dist/FOMC_RAG_Vote_Simulator.html`** | **主要展示**：輸入會前的總體與市場情境，BM25 從 244 場會議的 3,553 段原文檢索條件最接近的會議，推論政策方向、**逐一委員的投票**與每個判斷的原文出處 | 只要瀏覽器 |
| `src/scene/fomc-meeting-scene.html` | 同一組結果換成會議室的樣子：12 個席次、舉手的是投反對票的人，點任一位展開他為什麼這樣投 | 只要瀏覽器 |
| `src/app/` | R5 Streamlit 應用程式：Decision Replay、Assumption Monitor、Simulation & Evidence | Python 3.11 ＋ 4 個外部資料庫 |

離線頁面的索引已內嵌在 HTML 裡，第一次按「執行 RAG 情境預測」約 1 秒建索引，之後即時。
**整個模擬過程不呼叫任何 LLM**，全部是瀏覽器內的確定性算術——所以離線也跑得動，
而且同樣的輸入永遠得到同樣的輸出。

Streamlit 應用程式的安裝與啟動步驟，見 **[`docs/SETUP_zh-TW.md`](docs/SETUP_zh-TW.md)**。

---

## 我們怎麼得到這個結論

這不是「接一個 LLM 然後相信它」。結論是把**計量經濟模型、檢索式 ML、
多個 LLM 變體**放進同一組凍結的對照實驗跑出來的——而且我們自己設計了
一個用來拆穿自己的對照組。

**四條互相牽制的路線**

| 路線 | 做法 | 它會失敗在哪 |
| --- | --- | --- |
| **計量經濟模型** | pooled ordered logit，7 個總體變數（政策中值、失業率與 12 個月變化、CPI、薪資就業、10y–2y 利差、BAA 信用利差），嚴格 point-in-time vintage，121 場訓練 | 只看得到數字，看不到桌邊的人 |
| **檢索式 ML（RAG）** | BM25 檢索 3,553 段 FOMC 原文，矛盾條件重排後取最相近的 8 場類比會議 | 詞彙重疊沒有方向概念，會把「通膨低於目標」當成「高於目標」 |
| **LLM 委員模擬** | `gpt-5.6-terra` / `gpt-5.6-luna`，五階段結構化流程（profiles → openings → options → chair → votes），prompt 與 schema 全部凍結後才跑 | 記憶污染：它可能只是背過那場會 |
| **對照與消融實驗** | 45 場凍結測試集、3 組確定性基準 ＋ 5 組 LLM 變體，逐項拿掉具名、人物證據、反應模型 | ——這條就是用來檢查上面三條的 |

**實驗結果（45 場凍結測試集，[`src/app/artifacts/evaluation/r5_subscription_variant_matrix_v1.json`](src/app/artifacts/evaluation/r5_subscription_variant_matrix_v1.json)）**

| 變體 | 給模型看的東西 | 方向準確率 | 逐人異議 F1 |
| --- | --- | --- | --- |
| 多數類基準（全猜維持） | — | 62.2% | 不適用 |
| 持續性基準（照抄上一次） | — | 82.2% | 不適用 |
| pooled ordered logit | 總體數字 | 57.8% | 不適用 |
| **只給日期（記憶探針）** | **只有會議日期** | **91.1%** | 不適用 |
| naked frozen LLM | 總體數字 | 97.8% | 0.107 |
| 匿名委員 ＋ 反應模型 | 數字＋匿名人物＋反應模型 | 97.8% | 0.118 |
| 具名委員，不給反應模型 | 數字＋具名人物證據 | 93.3% | 0.286 |
| **具名委員 ＋ 反應模型** | 全部 | 97.8% | **0.311** |

**我們從這張表讀到的三件事**

1. **方向準確率是假的。** 只餵會議日期、什麼經濟數據都不給，LLM 照樣答對 91.1%。
   那 97.8% 裡絕大部分是背下來的，不是推論出來的——所以我們**不拿方向準確率當賣點**。
   自己設一個會打自己臉的對照組，是這個實驗唯一誠實的做法。
2. **真正的鑑別力在「誰反對」。** 異議 F1 從 0.107 拉到 0.311，是唯一沒被記憶污染
   吃掉的維度——因為逐人異議稀疏（base rate 4.4%），背不起來。
3. **有效的是「具名的人 ＋ 他自己的原文」，不是模型堆疊。** 匿名＋反應模型只有
   0.118，跟什麼都不給幾乎一樣；一旦把委員具名、把他過去說過的話接進去，
   即使不給計量模型也有 0.286。**人物證據才是訊號來源，反應模型是加分項。**

這也就是為什麼最後的產品長這樣：離線頁面把**計量方向**與**逐人票**分兩排並列、
不一致時直接跳警示，而每一位委員都可以點開他自己的原文出處。
我們相信的是第 3 點，不是那個好看的 97.8%。

完整回測、全部參數與其代價（包含我們**刻意不修**的那一項，以及為什麼修了會更糟）
寫在 [`docs/MODEL_zh-TW.md`](docs/MODEL_zh-TW.md)。

---

## 離線展示是怎麼運作的

上面那組實驗的結論，落地成一個開瀏覽器就能跑的確定性版本：

```
data/communications.csv     479 份 FOMC 文件（243 會議紀要 + 225 聲明 + 11 已排定）
        │                   雙重 UTF-8 編碼修復 → 解析
        ▼
src/retrieval/build_rag_index.py
        │                   切段、標條件標籤、抽異議句、判鷹鴿方向
        ▼
fomc_rag_index.json         244 場會議 · 3,553 段可檢索原文 · 68 位具名委員 · 88 筆異議
        │
        ▼
build_app.py  ──►  dist/FOMC_RAG_Vote_Simulator.html  （索引內嵌，離線可跑）
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

**方向與逐人票分開顯示，因為它們會吵架。** 計量模型與資料檢索各給一組機率，畫面
兩排並列；不一致時直接跳出警示，而不是給一個假裝有共識的數字。

---

## 專案結構

```
FOMC-Decision-Memory/
├── README.md                       ← 你在這裡
│
├── dist/
│   └── FOMC_RAG_Vote_Simulator.html  ★ 主要離線展示（開瀏覽器即可）
│
├── src/
│   ├── app/                        Streamlit 應用程式（1,005 檔，雜湊列管）
│   ├── retrieval/                  RAG 索引建置與回測評估腳本
│   └── scene/                      會議現場動畫
│
├── data/
│   └── communications.csv          479 份 FOMC 原始文件語料
│
├── release/                        釋出 manifest、SHA-256、IT 部署文件
│
└── docs/
    ├── SETUP_zh-TW.md              安裝、啟動、常見問題
    ├── MODEL_zh-TW.md              方法全文：模型、參數、回測、消融實驗與邊界
    ├── ENGINEERING_LOG_zh-TW.md    問題與解法摘要（開發過程）
    └── screenshots/
```

`dist/FOMC_RAG_Vote_Simulator.html` 是 `src/retrieval/build_app.py` 把
`fomc_rag_index.json` 與 R5 封存係數注進 `app_template.html` 產生的；改了索引或
參數就重跑 `cd src/retrieval && python3 build_app.py`。

### 文件

| | |
| --- | --- |
| [`docs/SETUP_zh-TW.md`](docs/SETUP_zh-TW.md) | Streamlit App 的安裝、啟動與常見問題 |
| [`docs/MODEL_zh-TW.md`](docs/MODEL_zh-TW.md) | 方法全文：四條路線的模型權重與全部參數、回測、消融實驗矩陣與方法邊界 |
| [`docs/ENGINEERING_LOG_zh-TW.md`](docs/ENGINEERING_LOG_zh-TW.md) | 開發過程真正踩到的坑與修法，每項附可重跑的驗證 |

### 可重跑的驗證

這個 repo 的每個結構性宣稱都有對應指令，不必相信文件：

```sh
# 成品可從原始碼重建，且逐位元組相同
cd src/retrieval && python3 build_app.py

# payload 檔案清單與 manifest 完全一致（1,005 筆）
cd src/app && find . -type f -not -name '.DS_Store' -not -path '*/__pycache__/*' \
  | sed 's|^\./||' | LC_ALL=C sort \
  | diff - ../../release/01_Manifests-and-Integrity/SOURCE_FILES.txt

# 上面那張消融實驗表就是這個檔案，不是手打的（在 repo 根目錄執行）
python3 -c "import json;[print(f\"{r['variant_id']:32} acc={r['policy_accuracy']:.3f} f1={r['dissent_f1']}\") \
  for r in json.load(open('src/app/artifacts/evaluation/r5_subscription_variant_matrix_v1.json'))['rows']]"

# 釋出檔案未被竄改
cd release && shasum -a 256 -c 01_Manifests-and-Integrity/SHA256SUMS.txt

# 離線建置雜湊清單與封存 manifest 相符（需先建好 .venv，見 SETUP）
cd src/app && ../../.venv/bin/python -m decision_memory.submission_gate
```

### 資料在哪裡

4 個執行期 `.sqlite` 資料庫與 `official_documents/` **刻意不在版本庫裡**，以
external frozen inputs 另外遞交。這不是檔案損壞：`RELEASE_MANIFEST.json` 的
`excluded` 明列 `"runtime databases"`，`SOURCE_FILES.txt` 也沒有任何 `.sqlite` 條目。
8 項檔案的 SHA-256 清單在
[`src/app/IT_DATA_INPUTS_zh-TW.md`](src/app/IT_DATA_INPUTS_zh-TW.md)。

同樣地，`artifacts/codex_subscription/` 與 `artifacts/llm_preflight/`（385 個封存的
模型 run，共 360 MB）由 payload 自己的 `.gitignore` 排除，改由 artifact manifest
治理。**上面兩個離線頁面不需要其中任何一項。**

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
