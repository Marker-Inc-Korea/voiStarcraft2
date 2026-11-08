import Foundation
import CoreGraphics
// usage: oscontrol click X Y | type TEXT | warp X Y
let a = CommandLine.arguments
func post(_ e: CGEvent?) { e?.post(tap: .cghidEventTap) }
switch a.count > 1 ? a[1] : "" {
case "warp": CGWarpMouseCursorPosition(CGPoint(x: Double(a[2])!, y: Double(a[3])!))
case "click":
  let p = CGPoint(x: Double(a[2])!, y: Double(a[3])!)
  CGWarpMouseCursorPosition(p); usleep(80_000)
  post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)); usleep(60_000)
  post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)); usleep(60_000)
  post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left))
case "type":
  for u in a[2].utf16 { var c = u; for down in [true,false] { let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down); e?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &c); post(e); usleep(20_000) } }
default: print("usage")
}
