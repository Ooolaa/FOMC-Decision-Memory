// Pull stills out of the finished file at the section boundaries, so the check
// is on the exported video rather than on the frames that went into it.
import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

let here = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
let url = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1]
              : "FOMC_Demo_zh-TW.mp4", relativeTo: here)
let asset = AVURLAsset(url: url)
let dur = try await asset.load(.duration)
let v = try await asset.loadTracks(withMediaType: .video)
let a = try await asset.loadTracks(withMediaType: .audio)
print("duration \(String(format: "%.2f", dur.seconds))s  video tracks \(v.count)  audio tracks \(a.count)")
if let t = v.first {
    let size = try await t.load(.naturalSize)
    let fps = try await t.load(.nominalFrameRate)
    print("video \(Int(size.width))x\(Int(size.height)) @ \(String(format: "%.1f", fps)) fps")
}
if let t = a.first {
    let d = try await t.load(.timeRange)
    print("audio spans \(String(format: "%.1f", d.start.seconds))s -> \(String(format: "%.1f", d.end.seconds))s")
}
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.4, preferredTimescale: 600)
for (label, t) in [("S1", 8.0), ("S3", 55.0), ("S4", 95.0), ("S5", 125.0), ("S6", 155.0)] {
    let (img, _) = try await gen.image(at: CMTime(seconds: t, preferredTimescale: 600))
    let out = here.appendingPathComponent("_check_\(label).jpg")
    guard let d = CGImageDestinationCreateWithURL(out as CFURL, "public.jpeg" as CFString, 1, nil)
    else { continue }
    CGImageDestinationAddImage(d, img, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
    CGImageDestinationFinalize(d)
    print("  \(label) @\(Int(t))s -> \(img.width)x\(img.height)")
}
