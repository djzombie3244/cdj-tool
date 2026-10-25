import AppKit
import Foundation
import WebKit

final class CDJToolApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let backend = Process()
    private var outputPipe: Pipe?
    private var outputBuffer = ""
    private var backendStarted = false
    private var shuttingDown = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        let controller = WKUserContentController()
        controller.add(self, name: "cdjQuit")
        controller.add(self, name: "cdjChoose")
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self

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

        launchBackend()
    }

    private func launchBackend() {
        let bundle = Bundle.main.bundleURL
        let contents = bundle.appendingPathComponent("Contents")
        let resources = contents.appendingPathComponent("Resources")
        let framework = contents.appendingPathComponent("Frameworks/Python3Bundled.framework/Versions/3.9")
        let python = framework.appendingPathComponent("bin/python3.9")
        let packages = resources.appendingPathComponent("python/site-packages")
        let ffmpeg = packages.appendingPathComponent("imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1")

        let support = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CDJ-Tool")
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let logURL = support.appendingPathComponent("CDJ-Tool.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)

        backend.executableURL = python
        backend.arguments = [resources.appendingPathComponent("app.py").path]
        backend.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        environment["PYTHONHOME"] = framework.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["CDJ_NATIVE"] = "1"
        environment["CDJ_BUNDLED"] = "1"
        environment["CDJ_SITE_PACKAGES"] = packages.path
        environment["CDJ_FFMPEG"] = ffmpeg.path
        backend.environment = environment
        backend.standardOutput = Pipe()
        backend.standardError = logHandle
        outputPipe = backend.standardOutput as? Pipe
        outputPipe?.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.consumeBackendOutput(line) }
        }

        backend.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self, !self.shuttingDown else { return }
                self.showMessage("CDJ Tool nie uruchomił składnika analizy. Szczegóły zapisano w logu aplikacji.")
            }
        }
        do {
            try backend.run()
            backendStarted = true
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

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        shuttingDown = true
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        if backend.isRunning { backend.terminate() }
    }
}

// Explicit entry point: the bundle has no main nib, so @NSApplicationMain would start without a delegate.
let application = NSApplication.shared
let appDelegate = CDJToolApp()
application.delegate = appDelegate
application.run()
