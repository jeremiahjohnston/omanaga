# Changelog

## 0.4.0

### Changed

- The plugin is now called Omanaga, ready for the Omarchy plugin marketplace. Its permanent id is `io.github.jeremiahjohnston.omanaga`, replacing `jeremiah.naga`. The README covers upgrading.
- The description now says plainly that it only supports the Razer Naga V2 HyperSpeed on its USB dongle, and that macros and lighting don't work.
- Saved layouts moved to `~/.local/share/omanaga/`. The old `~/.local/share/naga-control/` folder is moved over by itself the first time the plugin runs. The `NAGA_LAYOUTS` variable is now `OMANAGA_LAYOUTS`.
- The udev rule is now `70-omanaga.rules`. `setup.sh` removes the old one.
- `setup.sh` shows the exact rule and root commands and asks before doing anything, and `--yes` skips the question. It refuses a rule file that isn't exactly what it expects. `--remove` deletes the rule and reloads udev.
- `install.sh` is now `tools/dev-install.sh`, for development only, because it would collide with a plugin installed by `omarchy plugin add`. It refuses to copy over one.

### Security

- Saved layouts and `state.json` can only be read by you: the folder is 0700 and the files are 0600 from the moment they exist. Files from older versions are tightened on startup.
- The mouse lock no longer falls back to `/tmp`. It lives only in `XDG_RUNTIME_DIR` or `/run/user/<uid>`, is opened without following links, and must be a regular file owned by the right user. `tools/probe.py` under `sudo` never creates it, and warns and carries on if it's missing.
- A button's new setting goes to the backend over stdin instead of on the command line.
- The backend waits at most a minute for the mouse lock, and any run stops itself after 3 minutes.
- `layouts.json` and `state.json` are limited to 1 MiB, which also bounds what reaches the shell.
- The contributor notes moved from `CLAUDE.md` to `CONTRIBUTING.md`, so agents working in an installed plugin's folder don't pick them up automatically.
- CI checks that every Text in the panel is plain text, and tests `setup.sh` with stand-ins for root.

## 0.3.0

### Changed

- New panel layout. A strip of stat tiles sits under the title: battery, DPI, polling, sleep, Hypershift key and layout. Below it, Buttons and Device are on the left, DPI and Saved layouts on the right.
- Buttons are key caps, with a colored edge and tag for what each one does, and a legend. The editor is docked in the same card.
- The whole Buttons card glows while editing the Hypershift layer. The top buttons stay dimmed and locked there, as before.
- A Hypershift key on a button that can't use it gets a red ring, and its warning sits in the Buttons card.
- DPI shows the current value large, with a Custom tag, and the five levels as bars with their fields directly under them. Clicking a bar picks the level to use, replacing the separate 1 to 5 buttons. It still only applies when saved.
- Saved layouts are tiles, and the one on the mouse has a colored border and an "On mouse" badge instead of "(loaded)".
- The not-connected, setup and asleep states are a centered block with a mouse outline, and the asleep text says that commands can't wake the mouse.
- Colors come from the Omarchy theme, including its green, yellow, magenta and cyan, and animations stop when Omarchy's animations are off.

## 0.2.4

### Fixed

- After saving or deleting a layout when the mouse has gone to sleep or stopped answering, the layout list now updates straight away instead of showing the old names until the next refresh.
- When a layout load or factory reset fails partway, DPI and polling rate now go back before the buttons, because the rollback stops trying after 3 failures and each of these is a single write. The sleep timer still goes back last, because lowering it can put the mouse to sleep.
- Factory reset and layout delete each have their own 4 second confirm timer, so clicking one no longer changes how long the other stays armed.
- `tools/probe.py` no longer stops with a traceback if a device is unplugged while it looks for the mouse.

### Changed

- One fewer read-back for each button entry written, when writing the stored settings also updates the active ones. That saves 2 of 51 transactions on a typical button change and 12 of 101 on the Ctrl+1 to Ctrl+6 preset, about 4 to 12%. If the active settings don't follow, each entry written takes one more read instead.
- CI now runs pyflakes before the tests.
- The panel decodes the paths to its scripts, so it works from a folder with a space or other special character in its name.

## 0.2.3

### Changed

- Only Top button 1 and Top button 2 can be the Hypershift key now. Testing on the real mouse showed the other buttons store it but don't work as the key when held. The panel no longer offers it on them, and the backend refuses it.
- "While holding Hypershift" now edits only Side 1 to 12. Other buttons are dimmed in that view, and changing one on the normal layer now changes its Hypershift entry too, because nothing else can reach that entry.
- A key already on another button, from an old layout, still shows with a warning, and can be changed to something else. Loading a saved layout still restores exactly what it holds.

### Fixed

- "Level to use now" highlights a level only when the current DPI is that level's value. On any other DPI it highlights none and shows, for example, "Custom: 1234 DPI".
- Saving the DPI levels with none highlighted keeps the custom DPI instead of replacing it.
- Running the tests no longer resets `loaded` and `chosen` in the real `state.json`. The suite now works in a throwaway folder, and fails if it reaches the real data folder or lock file.

## 0.2.2

### Fixed

- A status 3 from the mouse is now retried instead of reported as "rejected". It's usually the dongle timing out on the radio link, and the next send works. A status 3 gets up to 5 sends, and the error appears only if all of them get a 3. A sleeping mouse still fails in under a second, after 3 sends.
- If the mouse falls asleep during the refresh after a command, the panel now says the change saved and to move the mouse to see the current settings. A new sleep time still reaches the sleep selector, whatever stopped the refresh.
- Lowering the sleep timer below the time the mouse has been idle can put it to sleep at once, which used to look like a failed save and start a rollback against a sleeping mouse. Setting the timer, loading a layout and factory reset now keep the change and say the mouse needs moving. Any other read-back failure still rolls back.
- Because the rollback uses the same retry, a link timeout during one no longer leaves a button half-changed. Anything that still can't go back is named in the error.

### Changed

- Added tests for how a sleeping mouse behaves: every command fails in under a second with the message to move it, on the first read and with nothing written.

## 0.2.1

### Fixed

- A Hypershift entry you set yourself no longer changes along with the normal action when the two happen to match. Setting Side 10 to Disabled on both layers and then to F15 on the normal layer used to overwrite the Hypershift entry too.
- The panel now waits for both the backend's exit and the end of its output before reading the result, because the two arrive in no guaranteed order.
- If `python3` can't start, the panel now says so and moves on to the next waiting command.
- The low battery alert now fires once per drop, however many monitors or shell restarts there are. The backend keeps track in `state.json`, and re-arms the alert once the battery is above 20%.

### Changed

- While the dongle is unplugged or setup is still needed, the panel checks again every 10 seconds instead of every 10 minutes, and refreshes itself if it's open when the mouse comes back.

## 0.2.0

### Fixed

- A failed layout load could leave buttons changed while saying nothing had changed. It now puts back the exact previous state.
- If something can't be put back, the error now names it and keeps the original cause, and the remaining settings still go back.
- Clearing the only Hypershift key and assigning a new one no longer wipes a customized Hypershift layer. That now includes entries deliberately set to Disabled or the factory action, which look unassigned on the mouse.
- The Ctrl+1 to Ctrl+6 preset now follows the Hypershift layer rules.
- Factory reset is now all or nothing.
- DPI, polling and sleep timer changes now go back on any failure, not only a mismatched read-back.
- A command that worked is no longer reported as failed when the refresh after it fails.
- Saving DPI levels now writes the DPI in all three slots of each stage, matching the factory format. Earlier versions zeroed the third, and the next save of DPI levels, layout load or factory reset repairs it.
- Button records are now read one layer at a time, so a stale reply from a button's other layer can't be taken for the one being read.
- Status 0 from the mouse now counts as still working, and a timeout is retried.
- The backend always answers the panel with JSON, so bad requests and unplugs no longer show as "unreadable".
- A layouts file that isn't valid is left alone instead of being overwritten, file errors no longer blame the mouse, and non-integer values in a layout are rejected.
- Changing one setting in the panel no longer discards unsaved edits elsewhere.
- Background battery checks no longer show errors in the panel.
- Panel messages now appear in a bordered strip above the columns, and layout results also appear in the Saved layouts section, because the old text could be hard to see.
- The button editor no longer overwrites actions it can't show.
- `tools/probe.py` stops with a clear message if the program it's piped into closes, and warns that Ctrl+digit buttons act as keys in the terminal during its key watch.

### Changed

- Much less traffic to the mouse: a button change takes about 50 transactions instead of 124, and a state read 40 instead of 78.
- Layouts the panel couldn't load back are refused when saving.
- The panel marks the layout you last loaded with "(loaded)", for as long as the mouse still matches it. Undoing a change so the mouse matches again brings the mark back. Saving over the loaded layout keeps its mark. Saving doesn't otherwise mark a layout, and factory reset and the Ctrl+1 to Ctrl+6 preset clear the mark.
- Saving over an existing layout name asks you to choose Replace or Cancel, and the typed name stays if the save fails.
- The panel shows when it's talking to the mouse.
- Listing and deleting layouts in the backend no longer need the mouse.
- The lock moved to `XDG_RUNTIME_DIR`, and `tools/probe.py` now takes it too.
- The layouts file is synced to disk before it replaces the old one.

### Removed

- `tools/set_ctrl_layout.py`. The panel's Ctrl+1 to Ctrl+6 preset does the same.

## 0.1.0

First release.
