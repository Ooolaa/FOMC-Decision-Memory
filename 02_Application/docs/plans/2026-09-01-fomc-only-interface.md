# FOMC-only 產品介面精簡計畫

## 目標

將 Streamlit 產品介面收旂為聯準會預測與會議證據，移除使用者可見的企業 synthetic/composite 示意案例、領域切換與企業審視操作。此次不破壞性刪除 SQLite 既有資料，以保留回復能力。

## 已驗證假設

- 企業示意功能的使用者入口位於 `app.py` 的領域選單、決策重播分支與假設監控區塊。
- FOMC 下次會議預測、166 場重播目錄、FOMC 假設監控與模擬證據不依賴企業 UI 分支。
- 凍結 UI rehearsal 與 immutable manifest 會綁定 `app.py`、截圖與 capture report hash，因此必須以新版本重建，不可覆寫 v21 證據。

## 依賴關係

```text
app.py FOMC-only navigation
  -> Streamlit regression tests
  -> four-view capture reports in three launch modes
  -> final UI rehearsal v2
  -> immutable offline manifest v22
  -> technical submission gate
```

## 里程碑

### M1 — 回歸契約

- 將 UI 測試改為要求無「領域」選單、無企業示意文字、仍可瀏覽 166 場 FOMC 會議。
- 將 rehearsal validator 的必要視圖改為四個 FOMC 視圖。

驗證：測試在修改 UI 前失敗，修改後通過。

### M2 — 最小 UI 刪除

- 移除領域選單與企業 replay/monitor/simulation 分支。
- 保留 FOMC 的頁面結構、AI 說明、166 場會議目錄與凍結評估矩陣。
- 移除因本次修改而成為無用的企業 UI loader、constant 與 review-write 控制。

驗證：Streamlit AppTest 四頁無 exception，且畫面不含企業示意案例。

### M3 — 證據鏈更新

- 將 capture script 改為只擷取四個 FOMC 視圖。
- 新增 v2 三模式 UI rehearsal，保留 v1 歷史證據。
- 建立 v22 portable manifest，更新 handoff 與 technical gate 指向。

驗證：三種啟動模式的四視圖 body/screenshot hash 一致；technical gate 全數 PASS。

### M4 — 完整驗收

- 執行全部 unittest、`git diff --check` 與 SQLite/gate 稽核。
- 以實際瀏覽器檢查首頁、會議重播與側邊欄，確認只剩 FOMC 內容。

回滾：還原本次 checkpoint；v21 證據與資料庫內既有資料不受影響。

## 風險與不處理範圍

- 本次不重訓預測模型，也不改變 Frozen 45 數值。
- 不刪除底層通用 schema 的 `domain`、`synthetic` 或 `composite` 欄位，因為 FOMC 模擬輸出仍需要 synthetic 來源邊界。
- 不刪除歷史 fixture 或資料庫記錄；使用者介面與新移交證據不再暴露它們。
