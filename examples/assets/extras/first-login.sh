#!/bin/sh
# An example first-login script: runs once for each user, as that user.
set -eu
mkdir -p "$HOME/.config"
printf 'first login done\n' >"$HOME/.config/linuxlive-example-first-login"
