// Assemble the demo video: captured frames + narration + burned-in subtitles.
//
//   swift build_video.swift [outfile.mp4]
//
// Reads manifest.json (frames and how long each is held) and narration.json
// (audio clips and subtitle cues, both timed from the measured voice track).
// Two passes: AVAssetWriter lays the frames onto a 1920x1080 timeline with the
// subtitle drawn in, then AVMutableComposition drops the narration in at its
// offsets and exports. There is no ffmpeg on this machine; AVFoundation is.

import AVFoundation
import CoreGraphics
import CoreText
import Foundation
import ImageIO

let W = 1920, H = 1080
let here = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
let out = URL(fileURLWithPath: CommandLine.arguments.count > 1
              ? CommandLine.arguments[1] : "FOMC_Demo_zh-TW.mp4",
              relativeTo: here).standardizedFileURL
let silent = here.appendingPathComponent("_video_only.mp4")

struct Frame: Decodable { let file: String; let dur: Double }
struct Clip: Decodable { let file: String; let start: Double; let dur: Double }
struct Cue: Decodable { let start: Double; let dur: Double; let text: String }
struct Narration: Decodable { let clips: [Clip]; let cues: [Cue]; let total: Double }

func die(_ m: String) -> Never { FileHandle.standardError.write(Data((m + "\n").utf8)); exit(1) }

let frames = try JSONDecoder().decode(
    [Frame].self, from: Data(contentsOf: here.appendingPathComponent("manifest.json")))
let narration = try JSONDecoder().decode(
    Narration.self, from: Data(contentsOf: here.appendingPathComponent("narration.json")))
guard !frames.isEmpty else { die("manifest.json has no frames") }

/// The cue on screen at time t, if any.
func cue(at t: Double) -> String {
    for c in narration.cues where t >= c.start && t < c.start + c.dur { return c.text }
    return ""
}

// ---- subtitle -------------------------------------------------------------

let subFont = CTFontCreateWithName("PingFangTC-Semibold" as CFString, 40, nil)
let subBoxWidth = CGFloat(W) - 420

/// Lay out one cue and return its rendered height, or draw it when ctx is set.
@discardableResult
func drawSubtitle(_ ctx: CGContext?, _ text: String) -> CGFloat {
    guard !text.isEmpty else { return 0 }
    var align = CTTextAlignment.center
    let para: CTParagraphStyle = withUnsafePointer(to: &align) { p in
        var setting = CTParagraphStyleSetting(
            spec: .alignment, valueSize: MemoryLayout<CTTextAlignment>.size, value: p)
        return CTParagraphStyleCreate(&setting, 1)
    }
    let attrs: [CFString: Any] = [
        kCTFontAttributeName: subFont,
        kCTForegroundColorAttributeName: CGColor(red: 1, green: 1, blue: 1, alpha: 1),
        kCTParagraphStyleAttributeName: para,
    ]
    guard let attr = CFAttributedStringCreate(nil, text as CFString, attrs as CFDictionary)
    else { return 0 }
    let fs = CTFramesetterCreateWithAttributedString(attr)
    let fit = CTFramesetterSuggestFrameSizeWithConstraints(
        fs, CFRange(location: 0, length: 0), nil,
        CGSize(width: subBoxWidth, height: .greatestFiniteMagnitude), nil)
    let textH = ceil(fit.height)
    guard let ctx else { return textH }

    let padX: CGFloat = 30, padY: CGFloat = 18, bottom: CGFloat = 52
    let boxH = textH + padY * 2
    let boxW = min(subBoxWidth, ceil(fit.width)) + padX * 2
    let box = CGRect(x: (CGFloat(W) - boxW) / 2, y: bottom, width: boxW, height: boxH)

    ctx.saveGState()
    ctx.setFillColor(CGColor(red: 0.02, green: 0.06, blue: 0.10, alpha: 0.82))
    ctx.beginPath()
    ctx.addPath(CGPath(roundedRect: box, cornerWidth: 10, cornerHeight: 10, transform: nil))
    ctx.fillPath()

    let textRect = CGRect(x: (CGFloat(W) - subBoxWidth) / 2, y: bottom + padY,
                          width: subBoxWidth, height: textH)
    let frame = CTFramesetterCreateFrame(
        fs, CFRange(location: 0, length: 0), CGPath(rect: textRect, transform: nil), nil)
    CTFrameDraw(frame, ctx)
    ctx.restoreGState()
    return textH
}

// ---- pass 1: frames -> silent video with subtitles -------------------------

try? FileManager.default.removeItem(at: silent)
let writer = try AVAssetWriter(outputURL: silent, fileType: .mp4)
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: W,
    AVVideoHeightKey: H,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: 9_000_000,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
    ],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(
    assetWriterInput: input,
    sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
        kCVPixelBufferWidthKey as String: W,
        kCVPixelBufferHeightKey as String: H,
    ])
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)

let space = CGColorSpaceCreateDeviceRGB()
var pool: CVPixelBufferPool?
CVPixelBufferPoolCreate(nil, nil, [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
    kCVPixelBufferWidthKey as String: W,
    kCVPixelBufferHeightKey as String: H,
    kCVPixelBufferCGImageCompatibilityKey as String: true,
] as CFDictionary, &pool)
guard let pool else { die("could not create pixel buffer pool") }

/// Draw a captured frame into a 1920x1080 buffer, letterboxed on black so a
/// clip of a different aspect is never stretched, with the subtitle on top.
func buffer(_ img: CGImage, _ subtitle: String) -> CVPixelBuffer? {
    var pb: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb)
    guard let pb else { return nil }
    CVPixelBufferLockBaseAddress(pb, [])
    defer { CVPixelBufferUnlockBaseAddress(pb, []) }
    guard let ctx = CGContext(
        data: CVPixelBufferGetBaseAddress(pb), width: W, height: H,
        bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(pb),
        space: space,
        bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue) else { return nil }
    ctx.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    let s = min(Double(W) / Double(img.width), Double(H) / Double(img.height))
    let w = Double(img.width) * s, h = Double(img.height) * s
    ctx.draw(img, in: CGRect(x: (Double(W) - w) / 2, y: (Double(H) - h) / 2, width: w, height: h))
    drawSubtitle(ctx, subtitle)
    return pb
}

let scale: Int32 = 600
var at = 0.0
var written = 0
var lastTick: Int64 = -1
/// Presentation times must be strictly increasing, and one cue ends exactly
/// where the next begins - so work in whole timescale ticks and drop any
/// boundary that lands on a tick already written.
func tick(_ seconds: Double) -> Int64 { Int64((seconds * Double(scale)).rounded()) }

for (i, f) in frames.enumerated() {
    let t0 = at, t1 = at + f.dur
    let url = here.appendingPathComponent("frames/\(f.file)")
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        die("could not read frames/\(f.file)")
    }
    // A still can be held for 15s and outlast several cues, so cut it at every
    // cue boundary inside its span and re-render with the subtitle of the day.
    var ticks: Set<Int64> = [tick(t0)]
    for c in narration.cues {
        for edge in [c.start, c.start + c.dur] where edge > t0 && edge < t1 {
            ticks.insert(tick(edge))
        }
    }
    for tk in ticks.sorted() where tk > lastTick {
        let start = Double(tk) / Double(scale)
        while !input.isReadyForMoreMediaData { usleep(3000) }
        guard let pb = buffer(img, cue(at: start + 0.002)) else { die("render failed") }
        adaptor.append(pb, withPresentationTime: CMTime(value: tk, timescale: scale))
        lastTick = tk
        written += 1
    }
    at = t1
    if i % 40 == 0 { FileHandle.standardError.write(Data("  frame \(i)/\(frames.count)\r".utf8)) }
}
input.markAsFinished()
writer.endSession(atSourceTime: CMTime(seconds: at, preferredTimescale: scale))

await withCheckedContinuation { (k: CheckedContinuation<Void, Never>) in
    writer.finishWriting { k.resume() }
}
if writer.status != .completed { die("video pass failed: \(writer.error?.localizedDescription ?? "?")") }
print("video pass: \(written) rendered from \(frames.count) captures, \(String(format: "%.1f", at))s")

// ---- pass 2: drop the narration in and export ------------------------------

let comp = AVMutableComposition()
let vTrack = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
let aTrack = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!

let vAsset = AVURLAsset(url: silent)
let vSource = try await vAsset.loadTracks(withMediaType: .video).first!
let vDur = try await vAsset.load(.duration)
try vTrack.insertTimeRange(CMTimeRange(start: .zero, duration: vDur), of: vSource, at: .zero)

for clip in narration.clips {
    let url = here.appendingPathComponent(clip.file)
    guard FileManager.default.fileExists(atPath: url.path) else { die("missing \(clip.file)") }
    let a = AVURLAsset(url: url)
    guard let src = try await a.loadTracks(withMediaType: .audio).first else { die("no audio in \(clip.file)") }
    let dur = try await a.load(.duration)
    try aTrack.insertTimeRange(
        CMTimeRange(start: .zero, duration: dur), of: src,
        at: CMTime(seconds: clip.start, preferredTimescale: scale))
}

try? FileManager.default.removeItem(at: out)
guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPreset1920x1080) else {
    die("could not create export session")
}
export.outputURL = out
export.outputFileType = .mp4
try await export.export(to: out, as: .mp4)

try? FileManager.default.removeItem(at: silent)
let attrs = try? FileManager.default.attributesOfItem(atPath: out.path)
let size = (attrs?[.size] as? Int) ?? 0
print("wrote \(out.lastPathComponent) — \(String(format: "%.1f", Double(size) / 1e6)) MB, "
      + "\(String(format: "%.1f", at))s, \(narration.cues.count) subtitles")
