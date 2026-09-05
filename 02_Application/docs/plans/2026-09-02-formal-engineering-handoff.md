# R5 正式工程交付包計畫（2026-09-02）

## 目標

從通過技術 gate 的固定 Git commit 與 annotated tag 建立可重現、可驗證的
本機工程交付資料夾；不得直接複製工作目錄或沿用 2026-09-01 的舊 ZIP。

## 已驗證假設

- 專案是 Python／Streamlit 的非 CRM 專案，正式驗證入口是
  `python -m unittest discover -s tests -q` 與
  `python -m decision_memory.submission_gate --scope technical`。
- 目前變更包含 AI 統整輸出上限 4,000 tokens 與其回歸測試；基礎預測、證據與
  SQLite 內容不應因本次交付而改寫。
- 原始碼 ZIP 排除 `.git`、金鑰、快取、日誌與 SQLite 執行資料；資料庫與模型
  成果屬部署時外部輸入，production data gate 標示為 `IT verification pending`。
- 暫定版本與 tag 為 `r5-handoff-2026.09.02`；建立 commit/tag 前另取使用者核准。

## 依賴圖

```text
current source + tests
  -> three-mode UI rehearsal
  -> immutable artifact manifest
  -> technical gate + full test suite
  -> reviewed source/secret/dependency checks
  -> explicit source-file staging
  -> local commit + annotated tag (approval required)
  -> deterministic tagged-source handoff builder
  -> ZIP/list/checksum/tag/tree verification
```

## 里程碑

1. 重新執行 normal、無 Process-scope API key、停止後重啟三種 UI rehearsal；
   每種模式均須 health `200/ok`，三頁 body-text 與 screenshot hash 等價。
2. 以新的 rehearsal 更新 immutable manifest；技術 gate 與完整測試均須 exit 0。
3. 盤點將納入 tag 的明確 source allowlist，排除所有 runtime/secret/cache 檔案；
   完成 dependency 與 secret 檢查。
4. 使用者核准後建立本機 commit 與 annotated tag；在該 exact commit 重跑 gate。
5. 從 tag 建立新交付資料夾並驗證 ZIP、`SOURCE_FILES.txt`、manifest 與 SHA-256。

## 風險與回復

- 工作目錄有大量未追蹤檔案，若沒有明確 allowlist，可能漏交或誤納入執行資料。
- UI 變更會使舊 rehearsal 與 manifest 失效；任何後續變更都必須重新凍結。
- rollback 是保留舊 tag 與舊交付資料夾；不覆寫、不刪除既有 package。

## 不在範圍

- 不 push、不部署、不建立 PR、不上傳檔案。
- 不存取正式客戶資料，不把 Windows User-scope `OPENAI_API_KEY` 放入交付包。
- 不宣稱正式主機或 production data 已驗證。
