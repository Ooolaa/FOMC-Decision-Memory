# 安裝與啟動 — Streamlit 應用程式

本文說明 `src/app/` 底下 R5 應用程式的安裝、啟動與疑難排解。
想先看不用安裝的離線展示，回到 [`../README.md`](../README.md)。

這是一套以 FOMC 為 benchmark、可離線展示的「AI 決策記憶系統」MVP。它把每次重大
決策保存成可追溯的 **DecisionTrace**：當時看見哪些資料、有哪些選項與爭論、最後
為何這樣決定、哪些假設支撐這個決定，以及誰支持或反對。系統另有 **Assumption
Monitor**，以預先登記的規則監看資料序列，在第一個反證出現時產生可稽核提醒。

App 共三個離線頁面：**Decision Replay**、**Assumption Monitor**、**Simulation & Evidence**。

---

## 一分鐘結論

**程式碼與環境都已就緒，但目前跑不出畫面 — 缺的不是程式碼，是資料。**

| 項目 | 狀態 |
| --- | --- |
| 應用程式原始碼（1,005 檔） | ✅ 完整，且與 manifest 逐檔相符 |
| Python 環境（macOS） | ✅ 已建好，8 個釘選版本全部精確安裝 |
| Streamlit 服務 | ✅ 可啟動，`127.0.0.1:8503` |
| **資料（4 個 `.sqlite`）** | ❌ **未包含在交付包，需另外索取** |

沒有資料庫時，首頁會顯示：

```
系統資料載入失敗：unable to open database file
```

> ⚠️ **注意：`/_stcore/health` 回 `200` 不代表畫面正常。** 實測即使 4 個 DB 全缺，
> health 仍回 200，但首頁仍會中止。要驗證畫面請看 `src/app/RUNBOOK.md`。

這是刻意的設計，不是檔案損壞。`RELEASE_MANIFEST.json` 的 `excluded` 明列
`"runtime databases"`，`SOURCE_FILES.txt` 也完全沒有 `.sqlite` 條目 — 執行期資料
一律以「external frozen inputs」另外遞交。

---

## 目錄結構

```
FOMC-Decision-Memory/
├── src/app/       應用程式本體 — 請勿搬動裡面的檔案
├── release/       釋出 manifest、雜湊、IT 部署文件
└── .venv/         Python 3.11 環境（自行建立，不進版本庫）
```

> ### ⚠️ 請勿重整 `src/app/` 內部
> App 全部以相對路徑解析資料：光是 `artifacts/` 就被引用 41 次，
> `document_manifests/` 12 次，`fixtures/` 9 次；連根目錄的 `.md` 文件都被
> `decision_memory/engineering_handoff.py` 列為必須位於根層的檔案。
> 搬動任何一項都會讓 App 與測試失效。

---

## 安裝與啟動

### 步驟 1 — 取得缺少的資料檔

向釋出者索取以下檔案。**這 8 項的 SHA-256 都列在
`src/app/IT_DATA_INPUTS_zh-TW.md`，拿到後務必先比對雜湊；同名但雜湊不同的
檔案不可替用。**

**App 啟動必要**（缺任一即無法顯示畫面），放入 `src/app/`：

| 檔案 | 用途 |
| --- | --- |
| `fomc_simulation.decision_trace_50_display.sqlite` | 預設唯讀展示庫，51 場完整 replay |
| `fred_fomc_real.sqlite` | FRED／FOMC point-in-time 來源庫，22 series、166 場會議 |
| `fomc_simulation.sqlite` | 正式凍結的離線 app DB |
| `fomc_simulation.transcript_segmentation_v3_candidate.sqlite` | 逐字稿 v3、1,736 筆逐人投票、103 筆 dissent |

**完整回歸測試另需**：

| 檔案 | 用途 |
| --- | --- |
| `fomc_simulation.vote_core_candidate.sqlite` | 投票核心驗證夾具 |
| `fomc_simulation.vote_labels_fixed_candidate.sqlite` | 投票標籤修復回歸夾具 |
| `artifacts/cache/fomc_2022_03_15_offline_baseline.json` | 離線 baseline 快取 |
| `official_documents/` | statement／minutes／逐字稿 PDF 原文目錄 |

**這些無法在本機重建。** `build_real_fred_db.py` 只能重建 `fred_fomc_real.sqlite`，
且它從 **Windows 登錄檔** 讀 `FRED_API_KEY`（`winreg.OpenKey(HKEY_CURRENT_USER,
"Environment")`），在 macOS 無法直接執行，也需要 FRED API key。其餘三個是付費模型
run 的產出。`fred_fomc_demo.sql` 只有 schema DDL，建出來是空表，不是可用的 demo。

### 步驟 2 — 啟動

**macOS** — 環境已建好，不需再安裝：

```sh
cd src/app
export PYTHONUTF8=1
export FOMC_APP_DB="$PWD/fomc_simulation.decision_trace_50_display.sqlite"
../../.venv/bin/python -m streamlit run app.py \
  --server.headless true --server.address 127.0.0.1 \
  --server.port 8503 --browser.gatherUsageStats false
```

**Windows** — 在專案根目錄開 PowerShell：

```powershell
cd src/app
.\run_app.ps1              # 或指定埠：.\run_app.ps1 -Port 8510
```

`run_app.ps1` 會先檢查 19 個必要檔案，缺哪一個都會直接指名報錯。

啟動後開啟 **<http://127.0.0.1:8503>**。

> 兩個平台都必須以 `src/app/` 作為工作目錄。

### 步驟 3 — （選用）AI 統整按鈕

核心預測、委員歷史投票與公開發言**不需要 API key**，頁面載入時也不會呼叫外部模型
或 FRED。只有按下「用 AI 統整預測理由」才會呼叫 Responses API。

若要啟用，依 `RUNBOOK.md` 規定：金鑰只允許 Windows **User-scope** 的
`OPENAI_API_KEY`，不得放進 repo、`.env` 或 Streamlit secrets。預設模型
`gpt-5.6-terra`，可用 `FOMC_AI_EXPLAIN_MODEL` 覆寫。

---

## 執行環境說明

| | |
| --- | --- |
| 直譯器 | Python 3.11.16 |
| 位置 | `.venv/`（專案根目錄） |
| 套件 | `requirements.txt` 全部 8 個釘選版本，完全一致 |

環境刻意建在 `src/app/` **之外**，讓 payload 與 `SOURCE_FILES.txt` 保持逐檔一致。

> ⚠️ **交付 zip 不含 `.venv/`。** venv 的每個執行檔都寫死了原機器的絕對路徑
> （shebang 指向 `/Users/…/.venv/bin/python3.11`），搬到別台機器
> 或別的目錄都不能用，硬打包只是多 612 MB 壞掉的位元組。請自行重建：

```sh
# 方式 A：已安裝 conda
conda create -p .venv python=3.11 -y

# 方式 B：已安裝 Python 3.11
python3.11 -m venv .venv

# 兩種方式共通的下一步
.venv/bin/python -m pip install -r src/app/requirements.txt
```

Windows 請改用 `.venv\Scripts\python.exe`。
**務必用 Python 3.11**，原因見下。

### 為什麼是 Python 3.11 而不是 3.12

`requirements.txt` 釘 `statsmodels==0.13.5`（2022 年 11 月），它只發佈到 **cp311**
的 wheel。在 3.12 上 pip 會退回原始碼編譯，並失敗於：

```
ModuleNotFoundError: No module named 'pkg_resources'
```

本機系統 Python 是 3.12.4，因此環境改用 3.11 建置，**保留原始釘選版本**，而不是
放寬 `statsmodels` 的版本。這一點與原稿「不要釘 0.13.5」的
建議不同：改用 3.11 之後就不需要放寬，原始的版本組合得以原樣重現。

環境以 conda prefix 建立即可完全自含；刪掉 `.venv/` 就乾淨移除，不動系統套件。

`statsmodels` 不是選用的：`app.py` → `next_meeting_forecast` → `reaction_model`
在模組載入時就 import 它，沒有它 App 無法啟動。

> 附註：`requirements.txt` 漏列 `altair`，但 `app.py` 有 import。目前它隨
> `streamlit` 一併裝入，所以本環境沒問題；若日後重建環境請留意。

---

## 主要文件

以下都在 `src/app/`：

| 用途 | 檔案 |
| --- | --- |
| 操作手冊 | `RUNBOOK.md` |
| 資料庫說明 | `DATABASE_GUIDE.md`、`IT_DATA_INPUTS_zh-TW.md` |
| Demo 腳本 | `DEMO_SCRIPT.md` |
| 專案理念與成果 | `HACKATHON_SUBMISSION.md` |
| **主程式進入點** | `app.py` |

IT 部署說明在 `release/02_IT-Deployment/IT_DEPLOYMENT_HANDOFF_zh-TW.md`。
該文件是雜湊在案的發行證據，**刻意未修改**；其中的目錄名稱描述的是本次重整前的
舊結構（`FOMC-Decision-Memory-R5-r5-handoff-2026.09.02/` → 現為 `src/app/`），
閱讀時以本文為準。

### 離線展示（後加，非原交付內容）

這些檔案不需要 Python、資料庫或網路，用瀏覽器直接開啟即可：

| 檔案 | 內容 |
| --- | --- |
| `dist/FOMC_RAG_Vote_Simulator.html` | **RAG 投票模擬**：以 BM25 檢索 `data/communications.csv` 的 3,553 段會議紀要／聲明，推論政策方向、逐一委員投票與原文出處 |

RAG 模擬的**模型權重、全部參數與方法邊界**寫在
[`MODEL_zh-TW.md`](MODEL_zh-TW.md)；建置腳本與回測評估腳本在 `src/retrieval/`。

**這個 RAG 模擬本身不呼叫任何 LLM**，全部是瀏覽器內的確定性算術。原 R5 才有用
LLM：`requirements.txt` 釘 `openai==2.32.0`，`decision_memory/codex_subscription.py`
另以 subprocess 呼叫 `codex` CLI 走 ChatGPT 訂閱路徑；預設模型
`gpt-5.6-terra`（`ai_member_explanation.py` 的 `DEFAULT_MODEL`，
`model_preflight.py` 會強制檢查），封存的 385 個 forecast run 全部是這個模型。

---

---

## 驗證完整性

確認釋出檔案未被竄改：

```sh
cd release
shasum -a 256 -c 01_Manifests-and-Integrity/SHA256SUMS.txt
```

應用程式 payload 共 1,005 個檔案，清單在
`release/01_Manifests-and-Integrity/SOURCE_FILES.txt`，路徑相對於 `src/app/`。

---

## 常見問題

| 症狀 | 原因與處理 |
| --- | --- |
| `unable to open database file` | 缺 4 個 `.sqlite` — 見步驟 1 |
| `Required MVP artifact is missing: …` | `run_app.ps1` 的前置檢查，訊息會指名缺哪個檔 |
| `ModuleNotFoundError: No module named 'streamlit'` | 用到系統 Python — 請用步驟 2 的直譯器路徑 |
| `statsmodels` 編譯失敗 | 你在 Python 3.12。`statsmodels==0.13.5` 只到 cp311，請用 3.11 |
| health 回 200 但畫面空白 | health 不檢查資料庫，仍是缺資料 |
| 畫面資料看起來不對 | 檢查 `FOMC_APP_DB`，它會覆寫預設展示庫 |
| 連接埠被占用 | 改用其他 `--server.port` / `-Port` |

