// Raticode's macOS speech helper: Apple's Speech framework, on-device only.
//
// The backend starts it as `raticode-speech listen` and pipes 16 kHz mono 16-bit PCM to
// stdin. It answers with one JSON object per line on stdout: ready, partial, final,
// error and done (see src/gofer/ui/system_speech.py). `raticode-speech check` reports
// whether dictation can work, without asking for permission or recording.
//
// macOS attributes the speech-recognition permission to Raticode.app, which launches
// this helper, so Raticode's Info.plist carries NSSpeechRecognitionUsageDescription.
// Recognition must run on the Mac: no audio is sent to Apple.

import AVFoundation
import Foundation
import Speech

setvbuf(stdout, nil, _IOLBF, 0)
signal(SIGPIPE, SIG_IGN)

func emit(_ fields: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: fields),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write(Data((line + "\n").utf8))
}

func fail(_ code: String, _ message: String) -> Never {
    emit(["type": "error", "code": code, "message": message])
    exit(3)
}

func permissionProblem(_ status: SFSpeechRecognizerAuthorizationStatus) -> (String, String)? {
    switch status {
    case .authorized:
        return nil
    case .denied:
        return ("denied", "Speech recognition is off for Raticode. Turn it on in System Settings > Privacy & Security > Speech Recognition.")
    case .restricted:
        return ("restricted", "Speech recognition is restricted on this Mac, for example by its administrator.")
    case .notDetermined:
        return ("not-asked", "macOS asks for permission to use speech recognition the first time you dictate.")
    @unknown default:
        return ("unknown", "macOS didn't allow speech recognition.")
    }
}

func languageName(_ locale: Locale) -> String {
    Locale.current.localizedString(forIdentifier: locale.identifier) ?? locale.identifier
}

func makeRecognizer() -> SFSpeechRecognizer? {
    SFSpeechRecognizer() ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
}

func offDeviceMessage(_ recognizer: SFSpeechRecognizer) -> String {
    "This Mac can't recognize \(languageName(recognizer.locale)) on the device yet. Turn on Dictation in System Settings > Keyboard and let it download the language, then try again."
}

func check() -> Never {
    let status = SFSpeechRecognizer.authorizationStatus()
    guard let recognizer = makeRecognizer() else {
        emit(["type": "status", "available": false, "reason": "macOS has no speech recognizer for your language."])
        exit(0)
    }
    var fields: [String: Any] = ["type": "status", "language": recognizer.locale.identifier]
    if status == .denied || status == .restricted, let problem = permissionProblem(status) {
        fields["available"] = false
        fields["reason"] = problem.1
    } else if !recognizer.supportsOnDeviceRecognition {
        fields["available"] = false
        fields["reason"] = offDeviceMessage(recognizer)
    } else {
        fields["available"] = true
        if status == .notDetermined, let problem = permissionProblem(status) { fields["reason"] = problem.1 }
    }
    emit(fields)
    exit(0)
}

/// One dictation, as a chain of recognition requests. A request that ends while audio is
/// still coming (a long pause, or a limit on its length) commits its words and the next
/// one starts, so nothing heard is dropped. All state lives on the main queue.
final class Dictation {
    let recognizer: SFSpeechRecognizer
    let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
    var request: SFSpeechAudioBufferRecognitionRequest?
    var task: SFSpeechRecognitionTask?
    var segment = 0
    var words = ""
    var started = Date()
    var quickFailures = 0
    var inputEnded = false

    init(recognizer: SFSpeechRecognizer) {
        self.recognizer = recognizer
    }

    func startSegment() {
        segment += 1
        let mine = segment
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        request.taskHint = .dictation
        if #available(macOS 13, *) { request.addsPunctuation = true }
        self.request = request
        words = ""
        started = Date()
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            DispatchQueue.main.async { self?.handle(mine, result, error) }
        }
    }

    func handle(_ mine: Int, _ result: SFSpeechRecognitionResult?, _ error: Error?) {
        guard mine == segment else { return }
        if let result {
            words = result.bestTranscription.formattedString
            if result.isFinal {
                quickFailures = 0
                commit()
                return
            }
            emit(["type": "partial", "text": words])
        }
        guard let error else { return }
        let heardSomething = !words.isEmpty
        if heardSomething || Date().timeIntervalSince(started) > 1 {
            quickFailures = 0
        } else {
            quickFailures += 1
        }
        if quickFailures >= 5 && !inputEnded {
            let reason = (error as NSError).localizedDescription
            commitWords()
            fail("recognizer", "macOS speech recognition stopped: \(reason)")
        }
        commit()
    }

    func commitWords() {
        let text = words.trimmingCharacters(in: .whitespacesAndNewlines)
        words = ""
        if !text.isEmpty { emit(["type": "final", "text": text]) }
    }

    /// The current request is over: keep its words, then finish or start the next one.
    func commit() {
        commitWords()
        task = nil
        request = nil
        segment += 1
        if inputEnded {
            emit(["type": "done"])
            exit(0)
        }
        startSegment()
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        request?.append(buffer)
    }

    func endInput() {
        inputEnded = true
        guard let request else {
            emit(["type": "done"])
            exit(0)
        }
        request.endAudio()
        let mine = segment
        // The last words normally arrive within a second; don't wait forever for them.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self, self.segment == mine else { return }
            self.commitWords()
            emit(["type": "done"])
            exit(0)
        }
    }

    func buffer(from data: Data) -> AVAudioPCMBuffer? {
        let frames = data.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let channel = buffer.floatChannelData?[0] else { return nil }
        buffer.frameLength = AVAudioFrameCount(frames)
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for index in 0..<frames {
                let sample = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))
                channel[index] = Float(sample) / 32_768
            }
        }
        return buffer
    }

    /// Reads audio on its own thread and hands it to the main queue.
    func readInput() {
        let thread = Thread { [self] in
            let input = FileHandle.standardInput
            var carry = Data()
            while true {
                let data = input.availableData
                if data.isEmpty { break }
                carry.append(data)
                let usable = carry.count - carry.count % 2
                guard usable > 0 else { continue }
                let chunk = carry.prefix(usable)
                carry = Data(carry.dropFirst(usable))
                if let buffer = buffer(from: Data(chunk)) {
                    DispatchQueue.main.async { self.append(buffer) }
                }
            }
            DispatchQueue.main.async { self.endInput() }
        }
        thread.start()
    }
}

var dictation: Dictation?

func listen(_ status: SFSpeechRecognizerAuthorizationStatus) {
    if let problem = permissionProblem(status) { fail(problem.0, problem.1) }
    guard let recognizer = makeRecognizer() else {
        fail("no-language", "macOS has no speech recognizer for your language.")
    }
    guard recognizer.supportsOnDeviceRecognition else { fail("no-on-device", offDeviceMessage(recognizer)) }
    guard recognizer.isAvailable else {
        fail("unavailable", "macOS speech recognition isn't available right now. Check that Dictation is on in System Settings > Keyboard.")
    }
    let session = Dictation(recognizer: recognizer)
    dictation = session
    session.startSegment()
    emit(["type": "ready", "language": recognizer.locale.identifier])
    session.readInput()
}

switch CommandLine.arguments.dropFirst().first ?? "listen" {
case "check":
    check()
case "listen":
    let status = SFSpeechRecognizer.authorizationStatus()
    if status == .notDetermined {
        // Shows macOS's permission dialog once; the answer arrives on another queue.
        SFSpeechRecognizer.requestAuthorization { answer in
            DispatchQueue.main.async { listen(answer) }
        }
    } else {
        DispatchQueue.main.async { listen(status) }
    }
    dispatchMain()
default:
    fail("usage", "Usage: raticode-speech check|listen")
}
