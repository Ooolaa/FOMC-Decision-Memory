# FOMC Decision Memory R5：部署資料輸入

狀態：`IT verification pending`。本文件描述工程交付包以外、部署時必須另外提供的
唯讀資料快照；原始碼 ZIP 不包含 SQLite、API key 或執行期寫入資料。

## 必要 SQLite 快照

| 檔案 | 用途 | SHA-256 |
| --- | --- | --- |
| `fred_fomc_real.sqlite` | FRED／FOMC point-in-time 來源庫 | `02f96292422ece4556e952902a4660c663652d9eaff8b470e75eec3dc7c91187` |
| `fomc_simulation.sqlite` | 正式凍結的離線 app DB | `83ef409125bea85f9463f2c1bf2c7a9accb46414d6e7268262b53c93a1c9732c` |
| `fomc_simulation.decision_trace_50_display.sqlite` | 預設唯讀展示庫 | `d60364029cafb6e79dc8b3e6a902b06016970620b827752f09f1d3df54139186` |
| `fomc_simulation.transcript_segmentation_v3_candidate.sqlite` | 投票與逐字稿候選資料 | `9be4bcf672b2f1dcf53f31a8fc985fb1acc02e9ed55a0de505bf1d82c7ebbcb3` |

完整回歸測試另需兩個凍結驗證輸入；正式 app 啟動不讀取它們：

| 檔案 | 用途 | SHA-256 |
| --- | --- | --- |
| `fomc_simulation.vote_core_candidate.sqlite` | 投票核心資料驗證夾具 | `4bb8f919b7933186986803fb569e8e84e0ab052f6edcf148f5d58e1a959f8212` |
| `fomc_simulation.vote_labels_fixed_candidate.sqlite` | 投票標籤修復回歸夾具 | `0a44b22b2321433a4d0bddbf28bba6d815ceb839eeefbc496ebcab02da10f014` |

這些檔案是凍結研究快照，不是正式企業資料庫的替代品。不得在部署時靜默換成測試
fixture、舊備份或同名但雜湊不同的檔案。

## 部署與權限

1. 把四個必要快照放在解壓後的專案根目錄，或由 `run_app.ps1 -AppDatabase <path>`
   明確指定展示 DB；正式部署不要依賴 launcher 的預設值。
2. 執行身分只需要讀取來源庫與展示庫。人工 review writes 預設關閉；若日後另行
   授權寫入，必須使用工作副本，不得改寫上述凍結快照。
3. `OPENAI_API_KEY` 只能來自 Windows User scope，不得放進原始碼、設定檔或交付包。
   核心預測與證據瀏覽不需要 API key；只有使用者主動按下 AI 統整時才使用。
4. 本版本沒有資料庫 migration。更新資料時應產生新快照、新雜湊與新 manifest，
   不得就地修改既有版本。

## IT 驗證

在部署主機執行：

```powershell
Get-FileHash -Algorithm SHA256 fred_fomc_real.sqlite,
  fomc_simulation.sqlite,
  fomc_simulation.decision_trace_50_display.sqlite,
  fomc_simulation.transcript_segmentation_v3_candidate.sqlite

python -m decision_memory.submission_gate --scope technical
```

預期四個 SHA-256 與上表完全相同，technical gate 輸出 `status=READY` 且 exit code 0。
若要跑完整 261 項回歸測試，還要放入上述兩個凍結驗證輸入與
`artifacts/cache/fomc_2022_03_15_offline_baseline.json`（SHA-256
`af39e1f87c09c428fc4481621c6e6fcef2616978797d7c9fd805b10b4bf0d0a6`）。
啟動後再檢查 `http://127.0.0.1:8503/_stcore/health` 回傳 `200` 與 `ok`。

## 備份與回復

快照是不可變輸入，因此備份與 rollback 都以完整檔案及其 SHA-256 為單位。若驗證
失敗，停止服務並換回上一個已知正確版本；不要對損壞或不明來源的 SQLite 就地修補。
