# FOMC Decision Memory R5 r5-handoff-2026.09.02 IT 部署交接說明

## 發行識別

- Profile：`generic`
- Git tag：`r5-handoff-2026.09.02`
- Git commit：`b745ef45391be7228129c4a6dbc09b5ebf596240`
- Git tree：`4774b2b72daa21623b23df90c05e6b21d106c35e`
- 產生時間：`2026-09-02T10:04:32.205216+00:00`

## 已提供

- `FOMC-Decision-Memory-R5-r5-handoff-2026.09.02/`（已解壓的來源樹，1090 個檔案）
- `SOURCE_FILES.txt`
- `RELEASE_MANIFEST.json`
- `SHA256SUMS.txt`

## 目前狀態

本目錄為**已解壓的本機工作副本**，不是可直接轉交的發行包。

- 原始 `FOMC-Decision-Memory-R5-r5-handoff-2026.09.02-source.zip` 已解壓後刪除。
- 解壓內容已逐檔以 zip 的 CRC32 與檔案大小核對，1090 個檔案全數相符，
  且與 `SOURCE_FILES.txt` 清單完全一致。
- `SHA256SUMS.txt` 現僅涵蓋隨附的三個中繼檔，可直接 `shasum -a 256 -c` 通過。
- `RELEASE_MANIFEST.json` 未修改，保留原始發行記錄；其中所記本文件的
  SHA-256 為本地編輯前的值，與 `SHA256SUMS.txt` 不同屬預期。
- 來源 ZIP 的 SHA-256（`383ee63a...f362905`）與 git 座標仍完整記錄於
  `RELEASE_MANIFEST.json`。

若要再次對外交付，請由 annotated tag `r5-handoff-2026.09.02` 重新產生來源 ZIP
（直接由 tag 產生，不是工作資料夾副本），以 `RELEASE_MANIFEST.json` 內的
SHA-256 核對後，再依下列步驟交付。

## 驗證摘要

- 測試：Clean tagged checkout with external frozen inputs: 261 tests ran in 75.886s, OK; technical gate 12/12 READY; eight pinned project packages match requirements. Shared Conda pip check reports unrelated global-package conflicts.
- 安全：Initial scan found one medium unauthenticated wildcard Streamlit binding. Fixed to 127.0.0.1 in launcher and config; final independent verification found no bypass or remaining concrete issue. Runtime netstat shows 127.0.0.1:8503 only; health 200 ok; high-confidence secret matches 0; forbidden release paths 0.

此摘要不代表已驗證正式主機、正式資料庫或正式客戶資料。

## IT 部署步驟

1. 驗證 `SHA256SUMS.txt`。
2. 解壓至新的版本目錄，不要覆蓋現行版本。
3. 依專案文件建立受支援的 runtime 並安裝 lockfile。
4. 從安全來源注入 secrets；不得把秘密寫回來源目錄。
5. 另行提供核准的 runtime data、模型或其他外部輸入。
6. 將寫入權限限制為服務帳號、系統及核准管理員。
7. 執行窄範圍連線、schema、health、登入、授權及關鍵路徑檢查。
8. 成功後切換流量，保留上一版本與資料備份供 rollback。

## 未包含

`.git`、`.env`、正式 secrets、runtime DB、客戶資料、模型 artifacts、
logs、cache、output 與本機 worktrees。

## Profile 注意事項

依專案文件配置 runtime、服務帳號、secrets、外部資料、health check 與 rollback。
