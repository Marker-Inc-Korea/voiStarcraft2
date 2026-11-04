// Moves the real macOS cursor: `warp_cursor X Y` (global display coordinates, same space as Electron DIP).
import CoreGraphics
import Foundation
let args = CommandLine.arguments
guard args.count == 3, let x = Double(args[1]), let y = Double(args[2]) else { FileHandle.standardError.write("usage: warp_cursor X Y\n".data(using: .utf8)!); exit(2) }
CGWarpMouseCursorPosition(CGPoint(x: x, y: y))
CGAssociateMouseAndMouseCursorPosition(1)
