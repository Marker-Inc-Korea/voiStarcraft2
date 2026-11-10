#!/bin/sh
# Builds the listen-only chord helper into electron/native/bin/chord_helper (architecture of the build machine).
# Needs the Xcode command line tools (swiftc). Run from anywhere: sh electron/native/build.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/bin"
swiftc -O "$here/chord_helper.swift" -o "$here/bin/chord_helper"
echo "built $here/bin/chord_helper"
