#!/bin/sh
# An example build script: runs as root while the image is built.
set -eu
mkdir -p /usr/share/linuxlive-example
printf 'Built for %s %s on %s.\n' "$LL_NAME" "$LL_VERSION" "$LL_BASE" >/usr/share/linuxlive-example/build-script.txt
