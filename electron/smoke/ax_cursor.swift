// Second cursor for QA: drives OUR OWN windows through the Accessibility API only (press / focus / set value / frame), so the
// user's real mouse and keyboard stay free and the frontmost app never changes. It never creates, posts or taps input events
// and never moves the cursor (guarded by electron/test/vcursor.test.js).
//
// Why Accessibility and not synthetic mouse events: on macOS 15, per-process event posting and the private SkyLight focus+post
// path delivered nothing to a background window (Electron and a native app alike); AXPress / AXValue did (docs/virtual-cursor.md).
//
//   swiftc -O electron/smoke/ax_cursor.swift -o /tmp/voi_ax_cursor
//   /tmp/voi_ax_cursor serve PID   -> JSON lines on stdin: {"op":"press"|"focus"|"set"|"frame","id":"<DOM id>","value":"..."}
//                                     answers one JSON line each: {"ok":bool,"op":..,"id":..,"frame":{x,y,w,h}?,"error":..?}
import ApplicationServices
import Foundation

setvbuf(stdout, nil, _IOLBF, 0)
let args = CommandLine.arguments
guard args.count == 3, args[1] == "serve", let pid = pid_t(args[2]) else {
    print(#"{"ok":false,"error":"usage: ax_cursor serve PID"}"#); exit(64)
}
let app = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(app, 2.0)
// Chromium builds its accessibility tree only when an assistive client asks for it.
AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)

func attr(_ e: AXUIElement, _ name: String) -> AnyObject? {
    var value: AnyObject?
    return AXUIElementCopyAttributeValue(e, name as CFString, &value) == .success ? value : nil
}

func find(_ e: AXUIElement, id: String, depth: Int = 0) -> AXUIElement? {
    if depth > 60 { return nil }
    if (attr(e, "AXDOMIdentifier") as? String) == id { return e }
    for child in (attr(e, "AXChildren") as? [AXUIElement] ?? []) {
        if let hit = find(child, id: id, depth: depth + 1) { return hit }
    }
    return nil
}

func frame(_ e: AXUIElement) -> [String: Double]? {
    guard let p = attr(e, "AXPosition"), let s = attr(e, "AXSize") else { return nil }
    var point = CGPoint.zero, size = CGSize.zero
    AXValueGetValue(p as! AXValue, .cgPoint, &point)
    AXValueGetValue(s as! AXValue, .cgSize, &size)
    return ["x": point.x, "y": point.y, "w": size.width, "h": size.height]
}

func reply(_ body: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: body)) ?? Data(#"{"ok":false}"#.utf8)
    print(String(decoding: data, as: UTF8.self))
}

// The tree appears asynchronously after AXManualAccessibility; wait until the app exposes a window (max ~4 s).
for _ in 0..<40 {
    if (attr(app, "AXWindows") as? [AXUIElement])?.isEmpty == false { break }
    usleep(100_000)
}
reply(["ok": true, "op": "ready", "pid": Int(pid), "trusted": AXIsProcessTrusted()])

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let req = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let op = req["op"] as? String, let id = req["id"] as? String else {
        reply(["ok": false, "error": "bad request"]); continue
    }
    var element: AXUIElement?
    for _ in 0..<20 {  // the renderer may still be building the node (up to ~2 s)
        element = find(app, id: id)
        if element != nil { break }
        usleep(100_000)
    }
    guard let el = element else { reply(["ok": false, "op": op, "id": id, "error": "not found"]); continue }
    var body: [String: Any] = ["op": op, "id": id]
    if let f = frame(el) { body["frame"] = f }
    switch op {
    case "frame":
        body["ok"] = body["frame"] != nil
    case "press":
        let status = AXUIElementPerformAction(el, kAXPressAction as CFString)
        body["ok"] = status == .success; body["status"] = Int(status.rawValue)
    case "focus":
        let status = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        body["ok"] = status == .success; body["status"] = Int(status.rawValue)
    case "set":
        _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let status = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, (req["value"] as? String ?? "") as CFString)
        body["ok"] = status == .success; body["status"] = Int(status.rawValue)
    default:
        body["ok"] = false; body["error"] = "unknown op"
    }
    reply(body)
}
