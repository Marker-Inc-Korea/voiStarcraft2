// chord_helper: listen-only watcher for the sketch chord (hold Control+Option and nothing else).
//
// It NEVER posts or synthesises events and NEVER moves/captures the cursor: the only CoreGraphics call that touches input is a
// listen-only event tap (CGEventTapOptions.listenOnly) on flagsChanged + keyDown. It writes line-delimited JSON to stdout:
//   {"status":"ready"}                              tap installed
//   {"status":"denied","reason":"input-monitoring"} the OS refused the tap (exit 3; the supervisor retries slowly)
//   {"chord":"down"} / {"chord":"up"}               Control+Option (and no Command/Shift) became held / was released
//   {"key":"other"}                                 another key was pressed while the chord was held (or Cmd/Shift joined it)
//
// usage: chord_helper [--prompt]   (--prompt asks macOS to show the Input Monitoring permission prompt once)
import Foundation
import CoreGraphics

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

if CommandLine.arguments.contains("--prompt") { _ = CGRequestListenEventAccess() }

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

guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: mask, callback: callback, userInfo: nil) else {
  emit("{\"status\":\"denied\",\"reason\":\"input-monitoring\"}")
  exit(3)
}
tapRef = tap
let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
emit("{\"status\":\"ready\"}")
update(CGEventSource.flagsState(.combinedSessionState))
signal(SIGTERM) { _ in exit(0) }
signal(SIGINT) { _ in exit(0) }
// Parent gone (Electron crashed): stop, never linger as an orphan.
let parent = getppid()
Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in if getppid() != parent { exit(0) } }
CFRunLoopRun()
