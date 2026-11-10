// Prints the CoreGraphics session flags the smoke scripts must check before any real-pointer test:
//   CGSSessionScreenIsLocked=1|0|absent   (the key exists only while the screen is locked; note the TWO S in CGSSession)
//   kCGSSessionOnConsoleKey=1|0|absent
import Foundation
import CoreGraphics
let dict = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
func show(_ key: String) -> String {
  guard let value = dict[key] else { return "absent" }
  if let n = value as? NSNumber { return n.boolValue ? "1" : "0" }
  return "\(value)"
}
print("CGSSessionScreenIsLocked=\(show("CGSSessionScreenIsLocked"))")
print("kCGSSessionOnConsoleKey=\(show("kCGSSessionOnConsoleKey"))")
