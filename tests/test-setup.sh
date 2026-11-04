#!/bin/bash
# shellcheck disable=SC2015,SC2016  # the fail helper always ends the run, and the stand-ins are meant to expand later
# Runs setup.sh with root, the system folder and the commands it calls swapped for stand-ins, so it changes nothing real.
cd "$(dirname "$(realpath "$0")")/.." || exit 1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAILED: $1"; [ -z "$2" ] || echo "$2"; exit 1; }

# As a normal user, on the real script: it shows the plan and stops.
if [ "$EUID" -ne 0 ]; then
  out=$(./setup.sh </dev/null); code=$?
  [ "$code" -eq 1 ] && [[ $out == *'TAG+="uaccess"'* ]] && [[ $out == *"Run it with sudo"* ]] || fail "without root" "$out"
fi
grep -Eq 'MODE=|GROUP=|OWNER=|RUN' 70-omanaga.rules && fail "the rule must only grant access through uaccess"
[ "$(grep -c 'TAG+="uaccess"' 70-omanaga.rules)" -eq 1 ] || fail "the rule must use uaccess"

mkdir -p "$work/bin" "$work/rules" "$work/plugin"
for tool in cat dirname realpath rm grep; do ln -s "$(command -v "$tool")" "$work/bin/$tool"; done
printf '#!/bin/bash\necho "$*" >> "%s/log"\n/usr/bin/cp "${@: -2:1}" "${@: -1}"\n' "$work" > "$work/bin/install"
printf '#!/bin/bash\necho "udevadm $*" >> "%s/log"\n' "$work" > "$work/bin/udevadm"
chmod +x "$work/bin/install" "$work/bin/udevadm"
sed -e "s|^export PATH=.*|export PATH=$work/bin|" -e "s|^rules_dir=.*|rules_dir=$work/rules|" -e 's/\[ "\$EUID" -eq 0 \]/true/' setup.sh > "$work/plugin/setup.sh"
chmod +x "$work/plugin/setup.sh"
cp 70-omanaga.rules "$work/plugin/"
run() { "$work/plugin/setup.sh" "$@"; }

# Saying no, or having no answer, changes nothing.
out=$(echo n | run); code=$?
[ "$code" -eq 1 ] && [ ! -e "$work/log" ] && [ ! -e "$work/rules/70-omanaga.rules" ] || fail "answering no" "$out"
out=$(run </dev/null); code=$?
[ "$code" -eq 1 ] && [ ! -e "$work/log" ] || fail "no answer" "$out"

# Yes installs the rule with fixed ownership, and replaces the old one.
touch "$work/rules/70-naga-control.rules" "$work/rules/unrelated.rules"
out=$(echo y | run); code=$?
[ "$code" -eq 0 ] && cmp -s 70-omanaga.rules "$work/rules/70-omanaga.rules" || fail "answering yes" "$out"
[ ! -e "$work/rules/70-naga-control.rules" ] || fail "the old rule should go" "$out"
grep -q -- '-m 644 -o root -g root' "$work/log" || fail "install's ownership" "$(cat "$work/log")"
grep -q 'udevadm control --reload-rules' "$work/log" && grep -q 'udevadm trigger' "$work/log" || fail "udev reload" "$(cat "$work/log")"
rm "$work/rules/70-omanaga.rules" "$work/log"
out=$(run --yes </dev/null); code=$?
[ "$code" -eq 0 ] && [ -e "$work/rules/70-omanaga.rules" ] || fail "--yes" "$out"

# A rule that changes while it's being copied doesn't stay installed.
rm "$work/rules/70-omanaga.rules" "$work/log"
cp "$work/bin/install" "$work/install.real"
printf '#!/bin/bash\necho "$*" >> "%s/log"\necho junk > "${@: -1}"\n' "$work" > "$work/bin/install"
out=$(run --yes </dev/null); code=$?
[ "$code" -eq 3 ] && [ ! -e "$work/rules/70-omanaga.rules" ] && ! grep -q udevadm "$work/log" || fail "a swapped rule" "$out"
cp "$work/install.real" "$work/bin/install"
touch "$work/rules/70-omanaga.rules"

# A rule file that isn't exactly the expected one is refused.
rm "$work/rules/70-omanaga.rules" "$work/log"
echo 'SUBSYSTEM=="hidraw", MODE="0666"' >> "$work/plugin/70-omanaga.rules"
out=$(run --yes </dev/null); code=$?
[ "$code" -eq 3 ] && [ ! -e "$work/log" ] && [ ! -e "$work/rules/70-omanaga.rules" ] || fail "a changed rule" "$out"
ln -sf "$PWD/70-omanaga.rules" "$work/plugin/70-omanaga.rules"
out=$(run --yes </dev/null); code=$?
[ "$code" -eq 3 ] && [ ! -e "$work/log" ] || fail "a linked rule" "$out"
rm "$work/plugin/70-omanaga.rules"
out=$(run --yes </dev/null); code=$?
[ "$code" -eq 3 ] || fail "a missing rule" "$out"

# An unknown option does nothing.
out=$(run --nope </dev/null); code=$?
[ "$code" -eq 2 ] && [ ! -e "$work/log" ] || fail "an unknown option" "$out"

# --remove takes out both rules and nothing else, and doesn't need the rule file.
touch "$work/rules/70-omanaga.rules" "$work/rules/70-naga-control.rules"
out=$(run --remove </dev/null); code=$?
[ "$code" -eq 0 ] && [ ! -e "$work/rules/70-omanaga.rules" ] && [ ! -e "$work/rules/70-naga-control.rules" ] && [ -e "$work/rules/unrelated.rules" ] || fail "--remove" "$out"
grep -q 'udevadm control --reload-rules' "$work/log" || fail "--remove reload" "$out"
out=$(run --remove </dev/null); code=$?
[ "$code" -eq 0 ] || fail "--remove twice" "$out"
echo "all setup checks passed"
