#!/bin/bash
# For development only: copies this checkout into Omarchy's plugin folder, then restarts the shell to load it.
# To use the plugin normally, install it with `omarchy plugin add`, as the README says.
set -e
cd "$(dirname "$(realpath "$0")")/.."
dest="$HOME/.config/omarchy/plugins/io.github.jeremiahjohnston.omanaga"
if [ -e "$dest/.git" ]; then
  echo "$dest came from 'omarchy plugin add', and copying over it would leave it out of step with its repo."
  echo "Remove it first: omarchy plugin remove io.github.jeremiahjohnston.omanaga"
  exit 1
fi
mkdir -p "$dest"
cp manifest.json Panel.qml naga_ctl.py 70-omanaga.rules setup.sh "$dest/"
omarchy restart shell
echo "Installed to $dest"
