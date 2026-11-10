// Test-only pointer driver for smoke/sketch_probe.js (the supervisor's OS-level mouse; never part of the shipped app).
//   sketch_drag drag X1,Y1 X2,Y2 ...   press at the first point, drag through the rest in small steps, release at the last (real CGEvents)
//   sketch_drag click X,Y              press + release at the point (real CGEvents)
//   sketch_drag witness SECONDS        listen-only tap placed DOWNSTREAM of every session tap (annotated session tap, tail): prints `down X,Y` / `drag X,Y` / `up X,Y`
//                                      for each left-mouse event that actually reached the apps. A press the helper consumed is never printed.
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
case "click":
  let p = parse(a[1])
  post(.mouseMoved, p); usleep(60_000); post(.leftMouseDown, p); usleep(60_000); post(.leftMouseUp, p)
case "witness":
  setvbuf(stdout, nil, _IOLBF, 0)
  let seconds = Double(a.count > 1 ? a[1] : "10") ?? 10
  let mask: CGEventMask = (1 << CGEventType.leftMouseDown.rawValue) | (1 << CGEventType.leftMouseDragged.rawValue) | (1 << CGEventType.leftMouseUp.rawValue)
  let cb: CGEventTapCallBack = { _, type, event, _ in
    let l = event.location
    switch type {
    case .leftMouseDown: print("down \(Int(l.x)),\(Int(l.y))")
    case .leftMouseDragged: print("drag \(Int(l.x)),\(Int(l.y))")
    case .leftMouseUp: print("up \(Int(l.x)),\(Int(l.y))")
    default: break
    }
    fflush(stdout)
    return Unmanaged.passUnretained(event)
  }
  guard let tap = CGEvent.tapCreate(tap: .cgAnnotatedSessionEventTap, place: .tailAppendEventTap, options: .listenOnly, eventsOfInterest: mask, callback: cb, userInfo: nil) else { print("witness: no tap (Input Monitoring?)"); exit(3) }
  CFRunLoopAddSource(CFRunLoopGetCurrent(), CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0), .commonModes)
  CGEvent.tapEnable(tap: tap, enable: true)
  print("witness ready"); fflush(stdout)
  CFRunLoopRunInMode(.defaultMode, seconds, false)
default: print("usage: sketch_drag drag X,Y X,Y ... | click X,Y | witness SECONDS | pos")
}
