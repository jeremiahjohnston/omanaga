#!/bin/bash
# Fails if a Text or Label in a QML file can render markup. Without textFormat, Qt guesses from the text, so a layout
# name could be taken as HTML. The components built on them, like Info, are covered by their definitions.
file=${1:-"$(dirname "$(realpath "$0")")/../Panel.qml"}
awk '
  function count(s, ch) { return gsub(ch, "", s) }
  {
    line = $0
    gsub(/"([^"\\]|\\.)*"/, "\"\"", line)  # braces inside strings do not count
    sub(/\/\/.*/, "", line)
    if (!open && line ~ /(^|[^A-Za-z0-9_.])((Text|Label)|[A-Za-z_]+\.(Text|Label))[ \t]*\{/) { open = 1; depth = 0; start = FNR; plain = 0 }
    if (open) {
      if (line ~ /textFormat:[ \t]*Text\.PlainText/) plain = 1
      depth += count(line, "{") - count(line, "}")
      if (depth <= 0) {
        if (!plain) { print FILENAME ":" start ": Text or Label without textFormat: Text.PlainText"; bad = 1 }
        open = 0
        seen++
      }
    }
  }
  END {
    if (!seen) { print "No Text or Label found in " FILENAME ", so this check is broken."; exit 1 }
    if (open) { print FILENAME ":" start ": unbalanced braces"; exit 1 }
    if (!bad) print seen " Text and Label items are all plain text."
    exit bad
  }
' "$file"
