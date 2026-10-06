import Foundation
import Vision
import ImageIO

struct TextLine: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}
struct OCRResult: Codable {
    let engine: String
    let coordinate_system: String
    let lines: [TextLine]
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "LatteOCR", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Expected one image path"])
    }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = ["ko-KR", "en-US"].filter { supported.contains($0) }
    guard request.recognitionLanguages.contains("ko-KR") else {
        throw NSError(domain: "LatteOCR", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Korean OCR is unavailable"])
    }
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    let lines = (request.results ?? []).compactMap { observation -> TextLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return TextLine(text: candidate.string, confidence: candidate.confidence,
                        bbox: [Double(box.minX), Double(1 - box.maxY),
                               Double(box.width), Double(box.height)])
    }.sorted { $0.bbox[1] < $1.bbox[1] }
    let result = OCRResult(engine: "Apple Vision VNRecognizeTextRequest",
                           coordinate_system: "normalized_top_left_xywh", lines: lines)
    FileHandle.standardOutput.write(try JSONEncoder().encode(result))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("OCR failed: \(error.localizedDescription)\n".utf8))
    exit(1)
}
