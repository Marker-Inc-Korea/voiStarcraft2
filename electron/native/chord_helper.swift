// chord_helper: listen-only watcher for the sketch chord (hold Control+Option and nothing else).
//
// It NEVER posts or synthesises events and NEVER moves/captures the cursor: the only CoreGraphics call that touches input is a
// listen-only event tap (CGEventTapOptions.listenOnly) on flagsChanged + keyDown, plus (only while capture is on) the active mouse tap below. Stdout lines:
//   {"status":"ready"}                              tap installed
//   {"status":"denied","reason":"input-monitoring"} the OS refused the tap (exit 3; the supervisor retries slowly)
//   {"chord":"down"} / {"chord":"up"}               Control+Option (and no Command/Shift) became held / was released
//   {"key":"other"}                                 another key was pressed while the chord was held (or Cmd/Shift joined it)
//
// NATIVE MOUSE CAPTURE (sketch input). A mouse-down on any Electron window would activate our app and steal keyboard focus from the game,
// so while sketching main sends {"rect":{"x","y","w","h"}} (global screen points) and {"capture":true}; the helper then runs an ACTIVE tap on
// left mouse down/drag/up ONLY. A press that starts inside the rect is reported and CONSUMED (returned as nil: neither the game nor our windows
// see it) together with its drags and its release; everything outside the rect passes through untouched. It still never posts or synthesises
// an event and never moves the cursor. Needs the Accessibility permission (active tap):
//   {"accessibility":true|false}                    AXIsProcessTrusted at start and whenever capture is requested
//   {"capture":"on"|"off"|"unavailable"}            acknowledgement of {"capture":true|false}; unavailable = the active tap could not be created
//   {"mouse":"down"|"drag"|"up","x":..,"y":..,"t":ms}
// stdin (one JSON object per line): {"rect":{...}} {"capture":true|false}. EOF on stdin (parent gone) ends the helper.
//
// usage: chord_helper [--prompt]   (--prompt asks macOS to show the Input Monitoring / Accessibility prompts once)
import Foundation
import CoreGraphics
import ApplicationServices

setvbuf(stdout, nil, _IOLBF, 0)
func emit(_ line: String) { print(line); fflush(stdout) }

let chordMask: CGEventFlags = [.maskControl, .maskAlternate]
let extraMask: CGEventFlags = [.maskCommand, .maskShift]
var held = false

func isChord(_ flags: CGEventFlags) -> Bool { flags.contains(chordMask) && flags.intersection(extraMask).isEmpty }

func update(_ flags: CGEventFlags) {
  let now = isChord(flags)
  if now == held { return }
  if !now && flags.contains(chordMask) { emit("{\"key\":\"other\"}") } // Cmd/Shift joined the chord: not a sketch
  held = now
  emit(now ? "{\"chord\":\"down\"}" : "{\"chord\":\"up\"}")
}

if CommandLine.arguments.contains("--prompt") {
  _ = CGRequestListenEventAccess()
  _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
}

let mask: CGEventMask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue)
var tapRef: CFMachPort? = nil
let callback: CGEventTapCallBack = { _, type, event, _ in
  switch type {
  case .tapDisabledByTimeout, .tapDisabledByUserInput:
    if let tap = tapRef { CGEvent.tapEnable(tap: tap, enable: true) }
    update(CGEventSource.flagsState(.combinedSessionState)) // resync: a release may have happened while the tap was off
  case .flagsChanged:
    update(event.flags)
  case .keyDown:
    if held { emit("{\"key\":\"other\"}") }
  default: break
  }
  return Unmanaged.passUnretained(event) // listen-only: the event is never altered or consumed
}

// ---- native mouse capture (active tap, only while capture is on) ----------------------------------------------------------------------
var captureWanted = false      // main asked for capture
var grabbing = false           // a press inside the rect was consumed and its release has not arrived yet
var rect = CGRect.zero
var mouseTap: CFMachPort? = nil
var drainGuard: Timer? = nil

func msNow(_ event: CGEvent) -> Double { Double(event.timestamp) / 1_000_000.0 }
func emitMouse(_ kind: String, _ p: CGPoint, _ t: Double) { emit("{\"mouse\":\"\(kind)\",\"x\":\(p.x),\"y\":\(p.y),\"t\":\(t)}") }

let mouseCallback: CGEventTapCallBack = { _, type, event, _ in
  switch type {
  case .tapDisabledByTimeout, .tapDisabledByUserInput:
    if let tap = mouseTap, captureWanted || grabbing { CGEvent.tapEnable(tap: tap, enable: true) }
    return Unmanaged.passUnretained(event)
  case .leftMouseDown:
    let p = event.location
    if captureWanted && !grabbing && rect.contains(p) { grabbing = true; emitMouse("down", p, msNow(event)); return nil }
  case .leftMouseDragged:
    if grabbing { emitMouse("drag", event.location, msNow(event)); return nil }
  case .leftMouseUp:
    if grabbing {
      grabbing = false; emitMouse("up", event.location, msNow(event)); settleCapture(); return nil
    }
  default: break
  }
  return Unmanaged.passUnretained(event) // outside the rect (or capture off): untouched
}

func ensureMouseTap() -> Bool {
  if mouseTap != nil { return true }
  let m: CGEventMask = (1 << CGEventType.leftMouseDown.rawValue) | (1 << CGEventType.leftMouseDragged.rawValue) | (1 << CGEventType.leftMouseUp.rawValue)
  guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap, eventsOfInterest: m, callback: mouseCallback, userInfo: nil) else { return false }
  mouseTap = t
  CFRunLoopAddSource(CFRunLoopGetCurrent(), CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0), .commonModes)
  CGEvent.tapEnable(tap: t, enable: false)
  return true
}
// capture is off AND no consumed press is pending -> stop being in the event path and say so
func settleCapture() {
  if captureWanted || grabbing { return }
  drainGuard?.invalidate(); drainGuard = nil
  if let t = mouseTap { CGEvent.tapEnable(tap: t, enable: false) }
  emit("{\"capture\":\"off\"}")
}
func setCapture(_ on: Bool) {
  if on {
    let trusted = AXIsProcessTrusted()
    emit("{\"accessibility\":\(trusted)}")
    guard trusted, ensureMouseTap(), let t = mouseTap else { captureWanted = false; emit("{\"capture\":\"unavailable\"}"); return }
    captureWanted = true; grabbing = false
    CGEvent.tapEnable(tap: t, enable: true)
    emit("{\"capture\":\"on\"}")
  } else {
    captureWanted = false
    if grabbing {
      // keep consuming until the press that was consumed is released (so the game never sees an orphan mouse-up); never longer than 5 s
      drainGuard?.invalidate()
      drainGuard = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: false) { _ in grabbing = false; settleCapture() }
    } else { settleCapture() }
  }
}
func handleLine(_ line: String) {
  guard let data = line.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
  if let r = obj["rect"] as? [String: Any], let x = (r["x"] as? NSNumber)?.doubleValue, let y = (r["y"] as? NSNumber)?.doubleValue, let w = (r["w"] as? NSNumber)?.doubleValue, let h = (r["h"] as? NSNumber)?.doubleValue, w > 0, h > 0 {
    rect = CGRect(x: x, y: y, width: w, height: h)
  }
  if let on = obj["capture"] as? Bool { setCapture(on) }
}
var stdinBuffer = Data()
FileHandle.standardInput.readabilityHandler = { handle in
  let chunk = handle.availableData
  if chunk.isEmpty { exit(0) } // parent closed our stdin: it is gone
  stdinBuffer.append(chunk)
  while let nl = stdinBuffer.firstIndex(of: 0x0A) {
    let lineData = stdinBuffer.subdata(in: stdinBuffer.startIndex..<nl)
    stdinBuffer.removeSubrange(stdinBuffer.startIndex...nl)
    if let line = String(data: lineData, encoding: .utf8) { DispatchQueue.main.async { handleLine(line) } }
  }
  if stdinBuffer.count > 4096 { stdinBuffer.removeAll() }
}

guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: mask, callback: callback, userInfo: nil) else {
  emit("{\"status\":\"denied\",\"reason\":\"input-monitoring\"}")
  exit(3)
}
tapRef = tap
let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
emit("{\"status\":\"ready\"}")
emit("{\"accessibility\":\(AXIsProcessTrusted())}")
update(CGEventSource.flagsState(.combinedSessionState))
signal(SIGTERM) { _ in exit(0) }
signal(SIGINT) { _ in exit(0) }
// Parent gone (Electron crashed): stop, never linger as an orphan.
let parent = getppid()
Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in if getppid() != parent { exit(0) } }
CFRunLoopRun()
