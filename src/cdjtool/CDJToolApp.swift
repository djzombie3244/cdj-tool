import AppKit
import Foundation
import WebKit

/// WKWebView, ktory przyjmuje foldery przeciagniete z Findera (strona nie zna ich pelnych sciezek).
final class DropWebView: WKWebView {
    var onFolders: (([URL]) -> Void)?

    private func folders(_ sender: NSDraggingInfo) -> [URL] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        guard let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL] else {
            return []
        }
        return urls.filter { url in
            var isDirectory: ObjCBool = false
            return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
        }
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        return folders(sender).isEmpty ? super.draggingEntered(sender) : .copy
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        return folders(sender).isEmpty ? super.draggingUpdated(sender) : .copy
    }

    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        return folders(sender).isEmpty ? super.prepareForDragOperation(sender) : true
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let dropped = folders(sender)
        if dropped.isEmpty { return super.performDragOperation(sender) }
        onFolders?(dropped)
        return true
    }
}

final class CDJToolApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var webView: DropWebView!
    private var backend: Process?
    private var outputPipe: Pipe?
    private var outputBuffer = ""
    private var backendStarted = false
    private var shuttingDown = false
    private var restartCount = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        let controller = WKUserContentController()
        controller.add(self, name: "cdjQuit")
        controller.add(self, name: "cdjChoose")
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        webView = DropWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.onFolders = { [weak self] urls in self?.foldersDropped(urls) }

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1120, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "CDJ Tool"
        window.minSize = NSSize(width: 760, height: 600)
        window.center()
        window.contentView = webView
        window.delegate = self
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(systemDidWake),
            name: NSWorkspace.didWakeNotification,
            object: nil
        )

        launchBackend()
    }

    @objc private func systemDidWake() {
        guard !shuttingDown else { return }
        // Po wybudzeniu sprawdź czy backend nadal działa; jeśli nie — zrestartuj
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self, !self.shuttingDown else { return }
            if !(self.backend?.isRunning ?? false) {
                self.restartBackend()
            }
        }
    }

    private func restartBackend() {
        guard restartCount < 5 else {
            showMessage("CDJ Tool nie mógł uruchomić składnika analizy po kilku próbach. Zamknij i otwórz aplikację ponownie.")
            return
        }
        restartCount += 1
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        outputBuffer = ""
        backendStarted = false
        launchBackend()
    }

    private func launchBackend() {
        let bundle = Bundle.main.bundleURL
        let contents = bundle.appendingPathComponent("Contents")
        let resources = contents.appendingPathComponent("Resources")
        let framework = contents.appendingPathComponent("Frameworks/Python3Bundled.framework/Versions/3.9")
        let python = framework.appendingPathComponent("bin/python3.9")
        let packages = resources.appendingPathComponent("python/site-packages")
        #if arch(x86_64)
        let ffmpegName = "ffmpeg-macos-x86_64-v7.1"
        #else
        let ffmpegName = "ffmpeg-macos-aarch64-v7.1"
        #endif
        let ffmpeg = packages.appendingPathComponent("imageio_ffmpeg/binaries/" + ffmpegName)

        let support = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CDJ-Tool")
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let logURL = support.appendingPathComponent("CDJ-Tool.log")
        // Przy restarcie dopisuj do loga zamiast kasować
        if restartCount == 0 {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let logHandle = try? FileHandle(forWritingTo: logURL)
        if restartCount > 0, let handle = logHandle {
            handle.seekToEndOfFile()
        }

        let process = Process()
        backend = process

        process.executableURL = python
        process.arguments = [resources.appendingPathComponent("app.py").path]
        process.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        environment["PYTHONHOME"] = framework.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["CDJ_NATIVE"] = "1"
        environment["CDJ_BUNDLED"] = "1"
        environment["CDJ_SITE_PACKAGES"] = packages.path
        environment["CDJ_FFMPEG"] = ffmpeg.path
        process.environment = environment
        process.standardOutput = Pipe()
        process.standardError = logHandle
        outputPipe = process.standardOutput as? Pipe
        outputPipe?.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.consumeBackendOutput(line) }
        }

        process.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self, !self.shuttingDown else { return }
                // Jeśli backend zakończył się z kodem 0 lub -15 (SIGTERM od systemu przy usypianiu) — spróbuj restartu
                // Składnik analizy zakończył się, choć okno jest otwarte (np. po uśpieniu komputera).
                // Zawsze próbujemy go uruchomić ponownie; restartBackend() po 5 nieudanych próbach
                // z rzędu pokazuje komunikat. Licznik zeruje się po każdym udanym starcie (CDJ_URL).
                NSLog("CDJ Tool: backend exited (reason %ld, status %d), restarting",
                      proc.terminationReason.rawValue, proc.terminationStatus)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                    guard let self, !self.shuttingDown else { return }
                    self.restartBackend()
                }
            }
        }
        do {
            try process.run()
            if restartCount == 0 { backendStarted = true }
        } catch {
            showMessage("Nie udało się uruchomić dołączonego środowiska Pythona: \(error.localizedDescription)")
        }
    }

    private func consumeBackendOutput(_ text: String) {
        outputBuffer += text
        while let newline = outputBuffer.firstIndex(of: "\n") {
            let line = String(outputBuffer[..<newline]).trimmingCharacters(in: .whitespacesAndNewlines)
            outputBuffer = String(outputBuffer[outputBuffer.index(after: newline)...])
            if line.hasPrefix("CDJ_URL ") {
                backendStarted = true
                restartCount = 0  // reset licznika po udanym starcie
                let address = String(line.dropFirst("CDJ_URL ".count))
                if let url = URL(string: address) { webView.load(URLRequest(url: url)) }
            }
        }
    }

    private func showMessage(_ message: String) {
        let html = "<meta charset='utf-8'><body style='font:16px -apple-system;padding:30px'><h2>CDJ Tool</h2><p>\(message)</p></body>"
        webView.loadHTMLString(html, baseURL: nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "cdjQuit" {
            NSApp.terminate(nil)
            return
        }
        guard message.name == "cdjChoose", let kind = message.body as? String else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = (kind == "mp3")
        panel.prompt = "Wybierz"
        panel.message = kind == "mp3" ? "Wskaż jeden lub kilka folderów z plikami audio (Cmd lub Shift)" : "Wskaż cały pendrive"
        let start = kind == "mp3"
            ? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Music")
            : URL(fileURLWithPath: "/Volumes", isDirectory: true)
        if FileManager.default.fileExists(atPath: start.path) { panel.directoryURL = start }
        panel.begin { [weak self] response in
            guard let self else { return }
            guard response == .OK, !panel.urls.isEmpty else {
                self.webView.evaluateJavaScript("window.cdjFolderCancelled && window.cdjFolderCancelled()")
                return
            }
            let paths: [String] = panel.urls.map { $0.path }
            guard let json = try? JSONSerialization.data(withJSONObject: [kind, paths] as [Any]),
                  let args = String(data: json, encoding: .utf8) else { return }
            self.webView.evaluateJavaScript("window.cdjFoldersSelected(\(args)[0], \(args)[1])")
        }
    }

    /// Upuszczone foldery: katalog glowny dysku w /Volumes to pendrive, pozostale to foldery z muzyka.
    private func foldersDropped(_ urls: [URL]) {
        var music: [String] = []
        var stick: String? = nil
        for url in urls {
            let parts = url.standardizedFileURL.path.split(separator: "/")
            if parts.count == 2 && parts[0] == "Volumes" && stick == nil {
                stick = url.standardizedFileURL.path
            } else {
                music.append(url.path)
            }
        }
        let payload: [Any] = [music, stick ?? NSNull()]
        guard let json = try? JSONSerialization.data(withJSONObject: payload),
              let args = String(data: json, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.cdjUpuszczono && window.cdjUpuszczono(\(args)[0], \(args)[1])")
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        shuttingDown = true
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        if backend?.isRunning == true { backend?.terminate() }
    }
}

// Explicit entry point: the bundle has no main nib, so @NSApplicationMain would start without a delegate.
let application = NSApplication.shared
let appDelegate = CDJToolApp()
application.delegate = appDelegate
application.run()
