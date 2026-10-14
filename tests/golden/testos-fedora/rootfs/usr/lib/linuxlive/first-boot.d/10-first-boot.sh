#!/bin/sh
# An example first-boot script: runs once as root on an installed system.
set -eu
mkdir -p /var/lib/linuxlive-example
date -u +%FT%TZ >/var/lib/linuxlive-example/first-boot
