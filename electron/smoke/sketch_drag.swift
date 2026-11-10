// Test-only pointer driver for smoke/sketch_probe.js (the supervisor's OS-level mouse; never part of the shipped app).
//   sketch_drag drag X1,Y1 X2,Y2 ...   press at the first point, drag through the rest in small steps, release at the last (real CGEvents)
//   sketch_drag pos                    print the current pointer position as X,Y
import Foundation
import CoreGraphics
let a = Array(CommandLine.arguments.dropFirst())
func post(_ type: CGEventType, _ p: CGPoint) { CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap) }
func parse(_ s: String) -> CGPoint { let v = s.split(separator: ",").map { Double($0)! }; return CGPoint(x: v[0], y: v[1]) }
switch a.first ?? "" {
case "pos":
  let p = CGEvent(source: nil)?.location ?? .zero
  print("\(Int(p.x)),\(Int(p.y))")
case "drag":
  let pts = a.dropFirst().map(parse)
  guard pts.count >= 2 else { print("need 2+ points"); exit(2) }
  post(.mouseMoved, pts[0]); usleep(60_000)
  post(.leftMouseDown, pts[0]); usleep(60_000)
  for i in 1..<pts.count {
    let from = pts[i - 1], to = pts[i]
    let steps = max(4, Int(hypot(to.x - from.x, to.y - from.y) / 6))
    for s in 1...steps { let t = Double(s) / Double(steps); post(.leftMouseDragged, CGPoint(x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t)); usleep(8_000) }
  }
  usleep(60_000)
  post(.leftMouseUp, pts.last!)
default: print("usage: sketch_drag drag X,Y X,Y ... | pos")
}
