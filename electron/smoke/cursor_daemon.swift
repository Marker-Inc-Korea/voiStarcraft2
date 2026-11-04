// Long-running cursor positioner for smoke tests (CoreGraphics global coords == Electron DIP on macOS).
// stdin: one "X Y" per line -> warps there and prints "ok X Y". EOF/SIGTERM exits. It does NOT dissociate the mouse
// (the user keeps control of their pointer); the driver re-sends the pinned point every ~25 ms to win back drift.
import CoreGraphics
import Foundation
setvbuf(stdout, nil, _IOLBF, 0)
while let line = readLine() {
  let parts = line.split(separator: " ").compactMap { Double($0) }
  if parts.count == 2 { CGWarpMouseCursorPosition(CGPoint(x: parts[0], y: parts[1])); print("ok \(Int(parts[0])) \(Int(parts[1]))") }
}
