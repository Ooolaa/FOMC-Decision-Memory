# FOMC Decision Memory Lab — R5 交付說明

**請從這裡開始。** 本資料夾是 R5 版本（git tag `r5-handoff-2026.09.02`）的完整交付包。

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
| 應用程式原始碼（1,102 檔） | ✅ 完整，且與 manifest 逐檔相符 |
| Python 環境（macOS） | ✅ 已建好，8 個釘選版本全部精確安裝 |
| Streamlit 服務 | ✅ 可啟動，`127.0.0.1:8503` |
| **資料（4 個 `.sqlite`）** | ❌ **未包含在交付包，需另外索取** |

沒有資料庫時，首頁會顯示：

```
系統資料載入失敗：unable to open database file
```

> ⚠️ **注意：`/_stcore/health` 回 `200` 不代表畫面正常。** 實測即使 4 個 DB 全缺，
> health 仍回 200，但首頁仍會中止。要驗證畫面請看 `02_Application/專案導覽_zh-TW.md` §7.4。

這是刻意的設計，不是檔案損壞。`RELEASE_MANIFEST.json` 的 `excluded` 明列
`"runtime databases"`，`SOURCE_FILES.txt` 也完全沒有 `.sqlite` 條目 — 執行期資料
一律以「external frozen inputs」另外遞交。

---

## 目錄結構

```
FOMC-Decision-Memory-R5/
├── README_zh-TW.md      ← 你在這裡
├── AGENTS.md            給 AI coding agent 的工作守則（中文）
├── 01_Release-Handoff/  釋出 manifest、雜湊、IT 部署文件
├── 02_Application/      應用程式本體 — 請勿搬動裡面的檔案
└── 03_Environment/      macOS 執行環境（Python 3.11）
```

> 若要用 Claude Code 之類的 AI agent 處理本專案，請先讓它讀 `AGENTS.md`。
> 這個包是雜湊列管的凍結釋出，agent 若照直覺「整理」會靜默破壞驗證。

> ### ⚠️ 請勿重整 `02_Application/` 內部
> App 全部以相對路徑解析資料：光是 `artifacts/` 就被引用 41 次，
> `document_manifests/` 12 次，`fixtures/` 9 次；連根目錄的 `.md` 文件都被
> `decision_memory/engineering_handoff.py` 列為必須位於根層的檔案。
> 搬動任何一項都會讓 App 與測試失效。

---

## 安裝與啟動

### 步驟 1 — 取得缺少的資料檔

向釋出者索取以下檔案。**這 8 項的 SHA-256 都列在
`02_Application/IT_DATA_INPUTS_zh-TW.md`，拿到後務必先比對雜湊；同名但雜湊不同的
檔案不可替用。**

**App 啟動必要**（缺任一即無法顯示畫面），放入 `02_Application/`：

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
cd 02_Application
export PYTHONUTF8=1
export FOMC_APP_DB="$PWD/fomc_simulation.decision_trace_50_display.sqlite"
../03_Environment/.venv/bin/python -m streamlit run app.py \
  --server.headless true --server.address 127.0.0.1 \
  --server.port 8503 --browser.gatherUsageStats false
```

**Windows** — 在專案根目錄開 PowerShell：

```powershell
cd 02_Application
.\run_app.ps1              # 或指定埠：.\run_app.ps1 -Port 8510
```

`run_app.ps1` 會先檢查 19 個必要檔案，缺哪一個都會直接指名報錯。

啟動後開啟 **<http://127.0.0.1:8503>**。

> 兩個平台都必須以 `02_Application/` 作為工作目錄。

### 步驟 3 — （選用）AI 統整按鈕

核心預測、委員歷史投票與公開發言**不需要 API key**，頁面載入時也不會呼叫外部模型
或 FRED。只有按下「用 AI 統整預測理由」才會呼叫 Responses API。

若要啟用，依 `RUNBOOK.md` 規定：金鑰只允許 Windows **User-scope** 的
`OPENAI_API_KEY`，不得放進 repo、`.env` 或 Streamlit secrets。預設模型
`gpt-5.6-terra`，可用 `FOMC_AI_EXPLAIN_MODEL` 覆寫。

---

## 執行環境說明（`03_Environment/`）

| | |
| --- | --- |
| 直譯器 | Python 3.11.16 |
| 位置 | `03_Environment/.venv/` |
| 套件 | `requirements.txt` 全部 8 個釘選版本，完全一致 |
| 安裝紀錄 | `03_Environment/pip-install.log`、`conda-create.log` |

環境刻意建在 `02_Application/` **之外**，讓 payload 與 `SOURCE_FILES.txt` 保持
逐檔一致。

> ⚠️ **交付 zip 不含 `.venv/`。** venv 的每個執行檔都寫死了原機器的絕對路徑
> （shebang 指向 `/Users/…/03_Environment/.venv/bin/python3.11`），搬到別台機器
> 或別的目錄都不能用，硬打包只是多 612 MB 壞掉的位元組。請自行重建：

```sh
# 方式 A：已安裝 conda
conda create -p 03_Environment/.venv python=3.11 -y

# 方式 B：已安裝 Python 3.11
python3.11 -m venv 03_Environment/.venv

# 兩種方式共通的下一步
03_Environment/.venv/bin/python -m pip install -r 02_Application/requirements.txt
```

Windows 請改用 `03_Environment\.venv\Scripts\python.exe`。
**務必用 Python 3.11**，原因見下。

### 為什麼是 Python 3.11 而不是 3.12

`requirements.txt` 釘 `statsmodels==0.13.5`（2022 年 11 月），它只發佈到 **cp311**
的 wheel。在 3.12 上 pip 會退回原始碼編譯，並失敗於：

```
ModuleNotFoundError: No module named 'pkg_resources'
```

本機系統 Python 是 3.12.4，因此環境改用 3.11 建置，**保留原始釘選版本**，而不是
放寬 `statsmodels` 的版本。這一點與 `專案導覽_zh-TW.md` §7.1「不要釘 0.13.5」的
建議不同：改用 3.11 之後就不需要放寬，交付包的版本組合得以原樣重現。

環境以 conda prefix 建立（本機已有 conda），完全自含在此資料夾；刪掉
`03_Environment/` 即可乾淨移除，未變更任何系統套件。

`statsmodels` 不是選用的：`app.py` → `next_meeting_forecast` → `reaction_model`
在模組載入時就 import 它，沒有它 App 無法啟動。

> 附註：`requirements.txt` 漏列 `altair`，但 `app.py` 有 import。目前它隨
> `streamlit` 一併裝入，所以本環境沒問題；若日後重建環境請留意。

---

## 主要文件

以下都在 `02_Application/`：

| 用途 | 檔案 |
| --- | --- |
| **深入導覽（最詳細，強烈建議先讀）** | `專案導覽_zh-TW.md` |
| 操作手冊 | `RUNBOOK.md` |
| 資料庫說明 | `DATABASE_GUIDE.md`、`IT_DATA_INPUTS_zh-TW.md` |
| Demo 腳本 | `DEMO_SCRIPT.md` |
| 專案理念與成果 | `HACKATHON_SUBMISSION.md` |
| 稽核報告 | `R5_COMPLETION_AUDIT.md`、`R5_TECHNICAL_COMPLETION_AUDIT_2026-09-01.md`、`R5_CORRECTION_AUDIT_2026-08-31.md`、`DECISION_TRACE_HUMAN_REVIEW.md` |
| 投稿紀錄 | `SUBMISSION_CHECKLIST.md`、`SUBMISSION_RECORD.md` |
| 設計歷程 | `docs/plans/` |
| **主程式進入點** | `app.py` |
| 測試 | `tests/`（依領域分組） |

IT 部署說明在 `01_Release-Handoff/02_IT-Deployment/IT_DEPLOYMENT_HANDOFF_zh-TW.md`。
該文件記錄發行識別（git tag／commit／tree）、8 步 IT 部署程序，以及測試與安全
驗證摘要。**閱讀時請注意其中兩段描述的是整理前的舊結構**，見下方
「[已知過時描述](#已知過時描述)」。

### 套件根目錄的離線展示（後加，非原交付內容）

這些檔案不需要 Python、資料庫或網路，用瀏覽器直接開啟即可：

| 檔案 | 內容 |
| --- | --- |
| `FOMC_RAG_Vote_Simulator.html` | **RAG 投票模擬**：以 BM25 檢索 `communications.csv` 的 3,553 段會議紀要／聲明，推論政策方向、逐一委員投票與原文出處 |
| `FOMC_Vote_Scenario_Lab.html` | 情境投票預測（僅計量模型，無檢索） |

RAG 模擬的**模型權重、全部參數與方法邊界**寫在
`README_RAG_VOTE_SIMULATOR_zh-TW.md`；建置腳本與回測評估腳本在
`04_RAG_Vote_Simulator/`。兩份 HTML 與該目錄都是新增檔案，`02_Application/`
維持逐位元組不變。

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
cd 01_Release-Handoff
shasum -a 256 -c 01_Manifests-and-Integrity/SHA256SUMS.txt
```

應用程式 payload 共 1,102 個檔案，清單在
`01_Release-Handoff/01_Manifests-and-Integrity/SOURCE_FILES.txt`，路徑相對於
`02_Application/`。

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

---

## 本次整理做了什麼

原始交付包有一層同名巢狀資料夾
（`FOMC_Decision_Memory_R5_Formal_Handoff_2026-09-02/FOMC-Decision-Memory-R5-r5-handoff-2026.09.02/`），
已攤平成現在的結構。**沒有修改任何檔案內容。**

- 內層專案原封不動成為 `02_Application/`。
- 外層 4 個檔案移入 `01_Release-Handoff/`。
- 刪除已移除資料夾殘留的一個 macOS `.DS_Store`。
- 新增 macOS 執行環境 `03_Environment/`（在 payload 之外）。

### 已知過時描述

`IT_DEPLOYMENT_HANDOFF_zh-TW.md` 是雜湊在案的發行證據（列於 `SHA256SUMS.txt` 與
`RELEASE_MANIFEST.json`），因此**刻意不修改**。但它有兩段在本次整理後已與實際
狀況不符，閱讀時請以本文件為準：

| 該文件的描述 | 目前實際狀況 |
| --- | --- |
| 「已提供：`FOMC-Decision-Memory-R5-r5-handoff-2026.09.02/`（已解壓的來源樹，1090 個檔案）」 | 該目錄已更名為 `02_Application/`，內含 **1,102** 個檔案（測試檔重整與新增導覽文件所致，非本次整理造成） |
| 「`SHA256SUMS.txt` 現僅涵蓋隨附的三個中繼檔」 | 現涵蓋 **4** 筆，且路徑相對於 `01_Release-Handoff/`（含保留的原始 `SOURCE_FILES.txt`） |

其餘內容 — 發行識別、驗證摘要、8 步部署程序、未包含項目 — 均仍有效。
文中「本目錄為已解壓的本機工作副本，不是可直接轉交的發行包；若要再次對外交付，
請由 annotated tag 重新產生來源 ZIP」這項要求也**依然適用**。

### Manifest 重新產生（2026-09-04）

`SOURCE_FILES.txt` 與 `SHA256SUMS.txt` 依 `decision_memory/engineering_handoff.py`
（`_write_source_files` / `_write_checksums`）的原格式重新產生：UTF-8、LF、
結尾換行、位元組序排序、`<sha256>  <path>`。

- `SOURCE_FILES.txt` — 1090 → **1102** 筆。差異不是本次整理造成：2026-09-04
  時 67 個扁平的 `tests/test_*.py` 被重整為 11 個分類子目錄（另加 11 個
  `__init__.py`），並新增 `專案導覽_zh-TW.md`。3 個 `.DS_Store` 一律排除。
- `SHA256SUMS.txt` — 路徑改為相對於 `01_Release-Handoff/`。
- 重新產生前的原始檔保留在 `01_Release-Handoff/00_Superseded-r5-handoff-2026.09.02/`。

**`RELEASE_MANIFEST.json` 刻意未修改。** 它是綁定 git tag `r5-handoff-2026.09.02`
的釋出證據，記錄 `"tagged_file_count": 1090` 與當時 `SOURCE_FILES.txt` 的 SHA-256
（`9f906fe7…`）。該雜湊對上述保留的原始檔仍可驗證，釋出鏈完整。若為了配合目前
檔案樹而去改它，反而會誤述當初 tag 的內容。

另註：manifest 中 `IT_DEPLOYMENT_HANDOFF_zh-TW.md` 的條目（`2b0c6e95…`、2047
bytes）在本次整理**之前**就已與實際檔案不符 — 該文件於 2026-09-04 被修改，
當時的 `SHA256SUMS.txt` 已更新為 `7b533b23…`，那也是它目前的雜湊。
