# Demo 影片產出

**成品：`FOMC_Demo_zh-TW.mp4`** — 1920×1080、2 分 46 秒、H.264 + AAC 旁白，
中文字幕已燒進畫面，另有 `subtitles.srt` 可在 YouTube 另外上傳。標題／說明欄／章節時間碼在 `../VIDEO_SCRIPT_zh-TW.md`。

影片是**操作真實系統錄下來的**，不是簡報動畫：Playwright 驅動 `FOMC_RAG_Vote_Simulator.html`
與 `05_Design_Canvas/fomc-meeting-scene.html`，逐格擷取畫面；旁白用 macOS `say`
（zh_TW、Meijia、44.1 kHz）合成；最後用 AVFoundation 把畫格與音軌合成一個檔。
這台機器沒有 ffmpeg，整條管線只用系統內建的東西。

## 重做整支影片

```sh
cd /path/to/FOMC-Decision-Memory-R5
python3 -m http.server 8810 --bind 127.0.0.1 &   # 擷取腳本從這裡讀頁面

cd 06_Video_Assets
python3 build_narration.py --rate 200   # 旁白 → *.aiff + narration.json
node capture_frames.mjs                 # 操作系統 → frames/ + manifest.json（約 5 分鐘）
swift build_video.swift                 # 合成 → FOMC_Demo_zh-TW.mp4
swift verify_video.swift                # 檢查成片軌道並抽格
```

首次執行需要 `npm install`（`playwright@1.63.0`，已在 `package.json`）。

## 三支腳本各做什麼

| 檔案 | 作用 |
| --- | --- |
| `build_narration.py` | 從 `../VIDEO_SCRIPT_zh-TW.md` 抽出旁白，**逐句**用 `say` 合成、`afinfo` 量長度，寫出 `narration.json`（段落邊界＋音檔＋字幕時間）與 `subtitles.srt` |
| `capture_frames.mjs` | Playwright 操作兩個頁面，依分鏡擷取畫格；每格帶自己的停留秒數，段落總長＝該段旁白長度＋2.5 秒留白 |
| `build_video.swift` | AVAssetWriter 把畫格鋪成 1920×1080 影軌並用 CoreText 把字幕畫上去，AVMutableComposition 依 `narration.json` 把旁白放到對應時間點，匯出 mp4 |
| `verify_video.swift` | 讀成品回來，報告時長／軌道／解析度，並在各段落抽出靜格 |

**時間軸只有一個來源。** `build_narration.py` 量出每段旁白的實際長度後寫進
`narration.json`；擷取腳本用同一組數字決定每段畫面要撐多久。所以改了腳本重跑，
畫面與旁白一定同步，不需要在剪輯軟體裡對齊。

## 幾個實作上的坑

- **會議現場的 artboard 在 sandboxed iframe 裡**，載入後約 4 秒才掛載完成，太早擷取
  會拍到空白。腳本會輪詢到 12 個座位都出現才開始，並用畫面上的「重播會議」按鈕
  讓動畫從頭播，而不是 reload（reload 又要重等掛載）。
- **設計畫布有自己的工具列**，所以會議現場那段用 `clip: {x:43, y:48, w:1515, h:852}`
  只取 artboard 本身（正好 16:9）。artboard 尺寸若改，這組數字要跟著改。
- **擷取比動畫慢**：`live()` 會把每格「實際拍攝所花的時間」記為該格的停留時間，
  所以動畫播放速度是對的，只是格數少（約 2–3 fps）。靜止畫面則是一格撐十幾秒，
  整支片平均 2.6 fps——對這種以靜態畫面為主的 demo 是正常的。
- **`say` 預設 22 kHz**，`--data-format=BEI16@44100` 才能寫出 44.1 kHz 的 aiff
  （`LEI16` 會被 aiff 寫入器拒絕）。
- **`%` 要搬到數字前面**：中文唸「百分之七十四點二」，但 `%` 寫在數字後面，`say`
  照著唸會變成沒講完的「七十四點二百分之」。合成前用正則把 `74.2%` 轉成
  `百分之74.2`；字幕上仍然顯示 `74.2%` 的寫法。
- **一張停留十幾秒的靜止畫面會跨好幾條字幕**，所以合成時會依字幕邊界把它切成數格
  重畫（348 格擷取 → 391 格輸出）。切點必須量化到時間刻度：一條字幕的結束與下一條的
  開始是同一個時間點，浮點誤差會產生兩個幾乎相同的時間戳，AVAssetWriter 會直接拒收。

## 沒有留在版本庫裡的中間產物

`frames/`（約 79 MB）與 `node_modules/`（約 18 MB）是可重生的，交付時可以刪掉；
`manifest.json` 與 `frames/` 是一組，刪一個就要一起刪。`.aiff` 旁白檔留著，
因為自己配音時它們就是節奏基準。
