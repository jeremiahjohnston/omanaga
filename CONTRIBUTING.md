# Contributing to Omanaga

These are notes for anyone working on the plugin, people and AI agents alike. They are deliberately not in a `CLAUDE.md`: `omarchy plugin add` clones the whole repo, and marketplace reviewers object to instructions that agents would pick up automatically in an installed plugin's folder.

An Omarchy bar plugin that configures the Razer Naga V2 HyperSpeed and Naga V2 Pro on Linux, with no OpenRazer or kernel module. Settings are stored on the mouse.

`Panel.qml` is the UI. It runs `naga_ctl.py`, which talks to the dongle over hidraw and prints one JSON object per command. Saved layouts and `state.json` live in `~/.local/share/omanaga/` (owner-only).

## Test and try

- Tests use a simulated mouse: `python3 -I tests/test_ctl.py`. Never run them with `-O`, which strips the asserts they rely on.
- Install and try in the shell: `tools/dev-install.sh`. See the README for first-time setup. Other checks: `tests/test-setup.sh` and `tests/qml-plain-text.sh`.
- `tools/probe.py` is a read-only snapshot and key watch. It needs `sudo`.

## Hardware facts learned on the real mouse

- The third byte of a `0x8c` reply isn't the layer. It depends on the button and matches on both layers.
- DPI stages repeat the DPI three times.
- 1234 and 30000 DPI read back exactly.
- Polling rate codes are 8, 2 and 1, for 125, 500 and 1000 Hz.
- Typical timings: about 0.9s per command, and about 2 to 3.5s for a layout load.
- Status 3 after about 1s of busy is usually the dongle timing out on the radio link, not a refusal. It nearly always clears within a few sends: in a stress test on the real mouse, 4 of 13 needed the 3rd send. So it gets 5 sends, against 3 for anything else.
- Once asleep, the mouse answers every command with status 4 in about 20ms. Only moving it wakes it, commands never do.
- Lowering the sleep timer below the time since the last movement seems to put the mouse to sleep at once (seen once, going from 900 to 300).
- The mouse can't tell a Hypershift entry that was never set from one set to Disabled, so `state.json` records the ones the user chose.
- Only Top button 1 (`0x0b`) and Top button 2 (`0x0c`) work as the Hypershift key. Holding either one fired a Side 1 layer action. Side 12 and Middle click did not, even though the mouse stores `0c 01 01` on them without complaint.
- The old "Hypershift passes" check only compared bytes, so it can't show that a key works. Only the hand check can: hold the key and press a layer button.
- The "While holding Hypershift" layer is editable only for Side 1 to 12 (`0x40` to `0x4b`). That's a decision, based on the checks above.
- A custom DPI like 1234, which isn't any level, survives a power cycle.

## Omarchy shell facts

- The panel is created once per screen (`plugins/bar/Bar.qml`, a `Variants` over `Quickshell.screens`), so anything that must happen once, like the battery alert, belongs in the backend.
- `Process` and `StdioCollector` signals have no guaranteed order (see `plugins/panels/wifiqr/Panel.qml`). A result is only ready after both `exited` and `streamFinished`.
- If a program can't start, `started`, `exited` and `streamFinished` never fire. `running` just goes false.
- `Ui/ButtonGroup.qml` never assigns `value`, so `value:` bindings on it stay bound. Dropdowns do assign it, which is why `Panel.qml` has `Picker`.
- `barForeground` is defined on the base panel (`Ui/Panel.qml`).
- `Commons.Color` only exposes foreground, background, accent, urgent and muted. `Panel.qml` reads the theme's green, yellow, magenta and cyan from `colors.toml` itself, and rereads it when the accent or background changes.
- The shell's sources are on GitHub: `omacom/omarchy`, branch `quattro`, under `shell/`. Reduced motion is `Style.reduceMotion`.

## Workflow

- Run `git pull` before working. Code changes go through GitHub branches and PRs.
- Don't edit files another session is working on.
- Real-mouse testing happens on the desktop with the real mouse, in its checkout of this repo.

## Open items

Physical-press checks that need a human at the desktop with the real mouse:

- Media keys work.
- DPI up and down step through the levels from the current one.
- "Scroll left" and "Scroll right" set as a mouse action actually scroll.
- 1234 and 30000 DPI really change the pointer speed.
- Settings survive the mouse being switched off and on.
- In the panel: the Replace/Cancel prompt, the message strip and the "On mouse" badge look right, and the factory reset needs a double click.
- Waking a sleeping mouse from the panel works.
- In the panel: a Hypershift key on another button (loaded from an old layout) shows its warning and can be changed away, and the top buttons dim in the Hypershift view.
- Saving the levels at a custom DPI keeps that DPI, and DPI up and down still step from the active level afterwards.
- Under `sudo`, `tools/probe.py` finds the panel's lock file, or warns and carries on if there isn't one.
- `setup.sh --remove` on a real system.

Confirmed by hand on the desktop with 0.4.0:

- `omarchy plugin add` installs and enables the plugin.
- `setup.sh` shows the rule and commands, asks first, and replaces the old `70-naga-control.rules`.
- The panel has permission through the new rule.
- A layout saved under Naga Control is still listed after the move to `~/.local/share/omanaga/`.
- Saving a button works with its action going over stdin.
- A custom DPI (1200) shows as "Custom: 1200 DPI" with no level highlighted.
