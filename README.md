# Omanaga

An Omarchy bar plugin for configuring the Razer Naga V2 HyperSpeed on Linux. It saves settings to the mouse itself, so they keep working with no software running.

**It supports one mouse: the Razer Naga V2 HyperSpeed, on its 2.4 GHz USB dongle.**

- Bluetooth doesn't work. Settings can't be changed over it.
- Other Razer mice don't work.
- Macros don't work, because the mouse can't store them. There are no per-app profiles either, for the same reason.
- Lighting doesn't work, because the mouse has no customizable lighting.
- The scroll mode button under the wheel can't be programmed.

Unofficial and not affiliated with Razer.

## Requirements

- Omarchy 4 (Quattro) and its shell
- Python 3, standard library only
- `notify-send` (libnotify, included with Omarchy), for the low-battery alert

It needs no OpenRazer, no kernel module and nothing else installed. It never uses the network.

## Install

The repository's address is moving to `github.com/jeremiahjohnston/omanaga`. GitHub redirects the old address after the move.

```
omarchy plugin add https://github.com/jeremiahjohnston/omanaga --enable
sudo ~/.config/omarchy/plugins/io.github.jeremiahjohnston.omanaga/setup.sh
```

`setup.sh` is the plugin's only root step, and you only run it once. It shows the rule and commands below and asks before doing anything. `--yes` skips the question.

It checks that `70-omanaga.rules` is exactly what it expects, and refuses to continue otherwise. Then, as root, it only does this:

1. `install -m 644 -o root -g root` copies `70-omanaga.rules` to `/etc/udev/rules.d/`.
2. `rm -f` removes `/etc/udev/rules.d/70-naga-control.rules`, if an older version installed it.
3. `udevadm control --reload-rules` and `udevadm trigger --subsystem-match=hidraw --action=change` apply the rule.

The rule gives the person logged in at the seat access to the dongle's control interface, through `uaccess`. It doesn't open the device to other accounts. Replug the dongle if the panel still asks for setup.

The plugin itself runs as your user. Saving a setting also needs no root.

## Upgrading from Naga Control

```
omarchy plugin remove jeremiah.naga
omarchy plugin add https://github.com/jeremiahjohnston/omanaga --enable
sudo ~/.config/omarchy/plugins/io.github.jeremiahjohnston.omanaga/setup.sh
```

`setup.sh` replaces the old udev rule. Saved layouts move from `~/.local/share/naga-control/` to `~/.local/share/omanaga/` by themselves the first time the plugin runs.

## Removal

```
sudo ~/.config/omarchy/plugins/io.github.jeremiahjohnston.omanaga/setup.sh --remove
omarchy plugin remove io.github.jeremiahjohnston.omanaga
```

`--remove` deletes the udev rule and reloads udev. To also delete your saved layouts:

```
rm -rf ~/.local/share/omanaga
```

Settings already saved on the mouse stay there. The panel's factory reset puts them back before you remove the plugin.

## What it does

- Sets the 12 side buttons, two top buttons, middle click and wheel tilt to a key with modifiers, a mouse action, DPI up or down, media, Hypershift or disabled. Only the two top buttons can be the Hypershift key, and holding it changes only the side buttons
- Sets the current DPI directly, plus the five levels the DPI buttons step through
- Sets the polling rate and sleep timer, and shows the battery level
- Saves named layouts on the computer and loads them onto the mouse, which holds one at a time
- Sends a desktop alert when the battery reaches 15%

Left click, right click and the wheel can't be edited, so the mouse always keeps a working click. Every write is read back and undone if it fails. If something can't be put back, for example because the mouse stopped answering, the error says what. Loading a layout and factory reset are all or nothing.

Saved layouts are stored in `~/.local/share/omanaga/layouts.json`, and `state.json` beside it remembers which one was last loaded and which Hypershift entries you set yourself. The folder and files can only be read by you. A layout can only be saved if the panel could load it back, so a mouse set to something the panel doesn't support, like an unlisted polling rate, can't be saved until that setting is changed.

## Develop

See `CONTRIBUTING.md` for what's been learned about the mouse and the shell.

```
python3 -I tests/test_ctl.py
tests/test-setup.sh
tests/qml-plain-text.sh
```

The tests run against a simulated mouse and stand-ins for root, so they change nothing real. Run `tools/dev-install.sh` to copy a checkout into the plugin folder and try it in the shell. It refuses to copy over a plugin installed with `omarchy plugin add`, so remove that one first. Saving a file in the plugin folder hot-reloads the plugin but may not rerun its startup code, so restart the shell for a real test.

`tools/probe.py` is a read-only snapshot of the mouse's settings and a live view of the keys it sends. It's a development tool that the plugin never runs, and it needs `sudo`. It waits for the panel if the panel is talking to the mouse.

## Credits

The protocol details come from these projects:

- [razerqdhid](https://github.com/geezmolycos/razerqdhid)
- [OpenNaga](https://github.com/Zer0codestuff/OpenNaga)
- [mouseo](https://github.com/pengowray/mouseo)
- [OpenRazer](https://github.com/openrazer/openrazer)
