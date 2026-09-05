# AGENTS.md — AI agent 工作守則

這是一個**凍結**的釋出包，不是可自由修改的工作 repo。它由 git tag
`r5-handoff-2026.09.02` 產生，不含 `.git`，`02_Application/` 底下每個檔案都是
manifest 列管的 **payload**。請把它當成需要驗證的證據，而不是待整理的程式碼。

## 不變條件

`02_Application/` 必須剛好是 **1,102 個檔案**，與
`01_Release-Handoff/01_Manifests-and-Integrity/SOURCE_FILES.txt` 完全一致。

動工前後都要檢查：

```sh
cd 02_Application && find . -type f -not -name '.DS_Store' \
  -not -path '*/__pycache__/*' | sed 's|^\./||' | LC_ALL=C sort \
  | diff - ../01_Release-Handoff/01_Manifests-and-Integrity/SOURCE_FILES.txt && echo PAYLOAD OK
```

釋出檔案另有自帶雜湊：

```sh
cd 01_Release-Handoff && shasum -a 256 -c 01_Manifests-and-Integrity/SHA256SUMS.txt
```

**兩項驗證在你收工時都必須通過。**

## 工作守則

- **只新增，不修改。** 新檔案放在本檔案旁的套件根目錄；`02_Application/` 內
  維持逐位元組不變。
- **保留完整 payload。** 有 59 組、共 31.6 MB 的重複檔案，這是刻意設計：每個
  模型 run 語料自成一套，才能獨立雜湊驗證。
  `decision_memory/artifact_manifest.py` 與 `submission_gate.py` 會檢查這些雜湊。
- **維持目錄結構。** App 全以相對路徑取數：`artifacts/` 被引用 41 次、
  `document_manifests/` 12 次，`engineering_handoff.py` 還要求根目錄的 `.md`
  必須留在頂層。所有指令都以 `02_Application/` 為工作目錄。
- **使用指定直譯器**：`03_Environment/.venv/bin/python`（Python 3.11.16，
  8 個釘選版本完全一致），它刻意放在 payload 之外。若該路徑不存在（交付 zip
  不含 venv，因為它寫死了原機器的絕對路徑），依 `README_zh-TW.md`
  「執行環境說明」重建，**務必用 Python 3.11**。
- **收拾好再結束。** 在此執行 Python 會產生 `__pycache__/`，破壞不變條件，
  結束前請刪除。

## 刻意未包含

4 個 `.sqlite` 資料庫與 `artifacts/cache/fomc_2022_03_15_offline_baseline.json`
不在包內。`RELEASE_MANIFEST.json` 的 `excluded` 明列 `"runtime databases"`，
`SOURCE_FILES.txt` 也沒有任何 `.sqlite`；它們以 frozen inputs 另外遞交。
**這是預期狀態，不是檔案損壞。** 清單與雜湊見
`02_Application/IT_DATA_INPUTS_zh-TW.md`。

## 環境不會告訴你的事

- **`statsmodels==0.13.5` 只發佈到 cp311 wheel。** 在 Python 3.12 上 pip 會退回
  原始碼編譯，並失敗於 `ModuleNotFoundError: No module named 'pkg_resources'`。
  請用 Python 3.11。
- **4 個 DB 全缺時 `/_stcore/health` 仍回 200。** 200 只代表 server 起得來，
  不代表畫面正常；請驗證頁面本身。
- **`requirements.txt` 漏列 `altair`**，但 `app.py` 有 import；目前隨 Streamlit
  一併裝入。
- **尚未釐清**：`DATABASE_GUIDE.md` 寫 19 個 FRED series，`RUNBOOK.md` 與
  `專案導覽_zh-TW.md` 寫 22 個。請向釋出者確認，不要自行猜測。

## 接著讀

- `README_zh-TW.md` — 安裝、啟動、常見問題。
- `02_Application/專案導覽_zh-TW.md` — 最詳細的導覽：模組地圖、資料流動、
  大改動前必讀的五個地雷。
- `02_Application/RUNBOOK.md` — 操作手冊；`DATABASE_GUIDE.md` — 資料庫結構。
