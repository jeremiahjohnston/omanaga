#!/bin/bash
# The plugin's only root step: installs or removes the udev rule that lets the plugin reach the mouse without root.
# Usage: sudo setup.sh [--yes]   shows the rule and the commands, asks, then installs it
#        sudo setup.sh --remove  removes it
# This folder is user-writable, so nothing from it runs as root, and the rule is compared to the copy below.
set -e
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 022

rules_dir=/etc/udev/rules.d
installed="$rules_dir/70-omanaga.rules"
old_installed="$rules_dir/70-naga-control.rules"
reload=(udevadm control --reload-rules)
trigger=(udevadm trigger --subsystem-match=hidraw --action=change)
expected='# Lets the person logged in at this seat configure the Naga V2 HyperSpeed: its receiver'"'"'s control interface only.
# Two steps because udev requires all ATTRS in one rule to match the same parent device, and these live on different ones.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1532", ATTRS{idProduct}=="00b4", ENV{OMANAGA_CONTROL}="1"
SUBSYSTEM=="hidraw", ENV{OMANAGA_CONTROL}=="1", ATTRS{bInterfaceNumber}=="00", TAG+="uaccess"'

yes=false
remove=false
for arg in "$@"; do
  case "$arg" in
    --yes) yes=true ;;
    --remove) remove=true ;;
    *) echo "Usage: sudo $0 [--yes | --remove]"; exit 2 ;;
  esac
done

need_root() {
  [ "$EUID" -eq 0 ] || { echo "Nothing was changed. Run it with sudo: sudo $0 $*"; exit 1; }
}

if $remove; then
  echo "This runs as root:"
  echo "  rm -f $installed $old_installed"
  echo "  ${reload[*]}"
  echo "  ${trigger[*]}"
  need_root --remove
  rm -f "$installed" "$old_installed"
  "${reload[@]}"
  "${trigger[@]}"
  echo "Removed. Replug the dongle if the plugin can still reach the mouse."
  exit 0
fi

rule="$(dirname "$(realpath "$0")")/70-omanaga.rules"
if [ ! -f "$rule" ] || [ -L "$rule" ] || [ "$(cat "$rule")" != "$expected" ]; then
  echo "Refusing to install: $rule isn't exactly the rule this script expects. Nothing was changed."
  exit 3
fi

echo "This udev rule will be installed as $installed:"
echo
echo "$expected"
echo
echo "This runs as root, and nothing else does:"
echo "  install -m 644 -o root -g root $rule $installed"
[ ! -e "$old_installed" ] || echo "  rm -f $old_installed"
echo "  ${reload[*]}"
echo "  ${trigger[*]}"
need_root
if ! $yes; then
  read -r -p "Go ahead? [y/N] " answer || answer=""
  case "$answer" in
    y | Y | yes | YES) ;;
    *) echo "Nothing was changed."; exit 1 ;;
  esac
fi

install -m 644 -o root -g root "$rule" "$installed"
# The check above read a user-writable file, so check what actually landed, which only root can change.
if [ "$(cat "$installed")" != "$expected" ]; then
  rm -f "$installed"
  echo "The rule changed while it was being installed, so it was removed. Run this again."
  exit 3
fi
rm -f "$old_installed"
"${reload[@]}"
"${trigger[@]}"
echo "Done. If the plugin still says it has no permission, unplug and replug the dongle."
