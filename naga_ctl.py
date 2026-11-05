#!/usr/bin/env python3
"""Backend for the Omanaga plugin: the Naga V2 HyperSpeed on its dongle and the Naga V2 Pro on its Mouse Dock Pro.
Talks to the receiver over /dev/hidraw, prints one JSON object.

Commands: state | battery | select-mouse <hyperspeed|pro> | save-layout <name> | apply-layout <name> | delete-layout <name> | set-button <id> <layer> (the action as JSON on stdin) | set-dpi <d1,d2,d3,d4,d5> <active|keep> | set-current-dpi <dpi>
          set-poll <hz> | set-idle <seconds> | preset <factory|wow>
Every write is read back, and anything that fails is put back as it was, or the error says what couldn't be."""
import contextlib, fcntl, functools, glob, json, os, select, signal, stat, sys, tempfile, time

VID, TXN, LEN = 0x1532, 0x1f, 91
GET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x07
SET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x06
STORED, ACTIVE = 1, 0
LOCK_WAIT = 60  # seconds to wait for another program to finish with the mouse
INPUT_WAIT, INPUT_MAX = 5, 4096  # seconds and bytes for a button action on stdin
MAX_DATA_FILE = 1 << 20  # bytes in a saved file, which keeps what reaches the shell bounded too
MAX_RUN = 180  # seconds before a run stops itself, far beyond the slowest real command
STATUS = {3: "The mouse rejected the command.", 4: "The mouse didn't answer. Move it to wake it, then try again.",
          5: "The mouse doesn't support that command."}

# Left, right and the wheel are deliberately absent from every model: they can't be edited, so the mouse always keeps a working click.
# CONTROLS, KEY_BUTTONS, SIDE_BUTTONS, FACTORY and STAGE_COPIES are the selected model's, set by use_model.
KEYS = {**{chr(97 + i): 0x04 + i for i in range(26)},
        **{str((i + 1) % 10): 0x1e + i for i in range(10)},
        "Enter": 0x28, "Esc": 0x29, "Backspace": 0x2a, "Tab": 0x2b, "Space": 0x2c, "-": 0x2d, "=": 0x2e, "[": 0x2f,
        "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38, "Caps Lock": 0x39,
        **{f"F{i + 1}": 0x3a + i for i in range(12)},
        "Print Screen": 0x46, "Scroll Lock": 0x47, "Pause": 0x48, "Insert": 0x49, "Home": 0x4a, "Page Up": 0x4b,
        "Delete": 0x4c, "End": 0x4d, "Page Down": 0x4e, "Right": 0x4f, "Left": 0x50, "Down": 0x51, "Up": 0x52,
        "Num Lock": 0x53, "Num /": 0x54, "Num *": 0x55, "Num -": 0x56, "Num +": 0x57, "Num Enter": 0x58,
        **{f"Num {(i + 1) % 10}": 0x59 + i for i in range(10)}, "Num .": 0x63,
        **{f"F{i + 13}": 0x68 + i for i in range(12)}}
KEY_NAMES = {v: k.upper() if len(k) == 1 else k for k, v in KEYS.items()}
MODS = {"ctrl": 0x01, "shift": 0x02, "alt": 0x04, "super": 0x08}
MOUSE = {"left": 0x01, "right": 0x02, "middle": 0x03, "back": 0x04, "forward": 0x05, "scrollleft": 0x68, "scrollright": 0x69}
MOUSE_NAMES = {1: "Left click", 2: "Right click", 3: "Middle click", 4: "Back", 5: "Forward",
               9: "Wheel up", 0x0a: "Wheel down", 0x68: "Scroll left", 0x69: "Scroll right"}
MEDIA = {"play": 0xcd, "next": 0xb5, "prev": 0xb6, "mute": 0xe2, "volup": 0xe9, "voldown": 0xea}
MEDIA_NAMES = {0xcd: "Play/Pause", 0xb5: "Next track", 0xb6: "Previous track", 0xe2: "Mute", 0xe9: "Volume up", 0xea: "Volume down"}
KEY_BY_USAGE = {v: k for k, v in KEYS.items()}
MOUSE_BY_CODE = {v: k for k, v in MOUSE.items()}
MEDIA_BY_USAGE = {v: k for k, v in MEDIA.items()}
POLL = {125: 8, 500: 2, 1000: 1}
HYPERSHIFT = bytes([0x0c, 1, 1, 0, 0, 0, 0])
DISABLED = bytes(7)
SLEPT = "Saved. The mouse went to sleep straight away because the new sleep time is shorter than it has been idle; move it to wake it."
ASLEEP_AT_REFRESH = "Saved. The mouse has gone to sleep, so move it to see the current settings."
LOW_BATTERY, BATTERY_REARM = 15, 20
ATTEMPTS, LINK_ATTEMPTS = 3, 5  # a status 3 can take a few sends to clear, but a sleeping mouse should fail fast

def key_rec(usage, mods=0):
    return bytes([0x02, 0x02, mods, usage, 0, 0, 0])

def factory_sides(first, count):
    """Side buttons ship as 1 to 9, 0, - and =, in order."""
    return {first + i: bytes([0x02, 0x01, 0x00, u, 0, 0, 0]) for i, u in enumerate(
        [0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x2d, 0x2e][:count])}

COMMON_FACTORY = {0x03: bytes([0x01, 1, 3, 0, 0, 0, 0]),
                  0x34: bytes([0x0e, 1, 0x68, 0, 0x14, 0, 0]), 0x35: bytes([0x0e, 1, 0x69, 0, 0x14, 0, 0])}
COMMON_CONTROLS = {0x03: "Middle click", 0x34: "Tilt left", 0x35: "Tilt right"}

# Each model as the probe found it (tools/probe.py, tools/probe_pro.py). Both speak the same protocol on interface 0
# with transaction ID 0x1f; they differ in which buttons exist and how many copies of each DPI a level holds.
MODELS = {
    "hyperspeed": {
        "name": "Naga V2 HyperSpeed", "pids": (0x00b4,), "battery": "It takes one AA.",
        "controls": {**{0x40 + i: f"Side {i + 1}" for i in range(12)},
                     0x0b: "Top button 1", 0x0c: "Top button 2", **COMMON_CONTROLS},
        # The mouse stores a Hypershift key on any button, but only the top two work as one when held (tested by hand).
        "key_buttons": {0x0b, 0x0c},
        "side_buttons": {0x40 + i for i in range(12)},
        "factory": {**factory_sides(0x40, 12), 0x0b: bytes([0x06, 1, 1, 0, 0, 0, 0]),
                    0x0c: bytes([0x06, 1, 2, 0, 0, 0, 0]), **COMMON_FACTORY},
        "stage_copies": 3,  # the factory stages repeat the DPI in a third slot
        "plates": {12: "12 buttons"},
    },
    "pro": {
        # 0x00a4 is the Mouse Dock Pro: the mouse answers through it. It keeps a map for every side plate at once,
        # whichever is fitted: 0x40-0x4b for the 12-button plate, 0x50-0x55 for the 6 and 0x04-0x05 for the 2.
        # The two buttons behind the wheel and the one underneath ship as DPI and profile controls, but their factory
        # records weren't captured, so a factory reset turns them off rather than guessing.
        "name": "Naga V2 Pro", "pids": (0x00a4,), "battery": "Put it on the dock to charge.",
        "controls": {**{0x40 + i: f"Side {i + 1}" for i in range(12)},
                     **{0x50 + i: f"Side {i + 1} (6-button plate)" for i in range(6)},
                     0x04: "Side 1 (2-button plate)", 0x05: "Side 2 (2-button plate)", **COMMON_CONTROLS,
                     0x6d: "Top button 1", 0x60: "Top button 2", 0x0e: "Bottom button"},
        # Unlike the HyperSpeed, a held Hypershift key works on the side buttons and the wheel click (Side 12 and the
        # wheel click tested by hand; the other side buttons are the same kind of control).
        "key_buttons": {0x40 + i for i in range(12)} | {0x50 + i for i in range(6)} | {0x04, 0x05, 0x03},
        "side_buttons": {0x40 + i for i in range(12)} | {0x50 + i for i in range(6)} | {0x04, 0x05},
        "factory": {**factory_sides(0x40, 12), **factory_sides(0x50, 6), 0x04: bytes([0x01, 1, 4, 0, 0, 0, 0]),
                    0x05: bytes([0x01, 1, 5, 0, 0, 0, 0]), **COMMON_FACTORY, 0x6d: DISABLED, 0x60: DISABLED, 0x0e: DISABLED},
        "stage_copies": 2,  # the rest of each level reads back as zeros
        "plates": {12: "12 buttons", 6: "6 buttons", 2: "2 buttons"},
    },
}
PLATE_BUTTONS = {12: {0x40 + i for i in range(12)}, 6: {0x50 + i for i in range(6)}, 2: {0x04, 0x05}}

def use_model(key):
    global MODEL, CONTROLS, KEY_BUTTONS, SIDE_BUTTONS, FACTORY, STAGE_COPIES
    m = MODELS[key]
    MODEL, CONTROLS, KEY_BUTTONS, SIDE_BUTTONS, FACTORY, STAGE_COPIES = (
        key, m["controls"], m["key_buttons"], m["side_buttons"], m["factory"], m["stage_copies"])

use_model("hyperspeed")
FACTORY_DPI, FACTORY_ACTIVE, FACTORY_POLL, FACTORY_IDLE = [400, 800, 1600, 3200, 6400], 3, 1000, 300

class Err(Exception):
    pass

class Asleep(Err):
    """Every attempt got status 4, which is how a sleeping mouse answers."""

def error_text(e):
    if isinstance(e, OSError):
        return f"Couldn't reach the mouse: {e.strerror or e}"
    return str(e)

def undo(original, steps):
    """Runs every (label, step) even if one fails, then raises the error that started it, adding what didn't go back.
    Gives up after a few failures, since each one can take seconds if the mouse has gone away."""
    failed = []
    for label, step in steps:
        if len(failed) >= 3:
            failed.append(label)
            continue
        try:
            step()
        except Exception:
            failed.append(label)
    if not failed:
        raise original
    names = list(dict.fromkeys(failed))
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    raise Err(f"{error_text(original)} These may not be back as they were: {shown}.") from original

def report(cls, cmd, size, args=b""):
    body = bytearray(90)
    body[1], body[5], body[6], body[7] = TXN, size, cls, cmd
    body[8:8 + len(args)] = args
    c = 0
    for b in body[2:88]:
        c ^= b
    body[88] = c
    return bytes(body)

class Hid:
    slept = None  # the sleep time, if setting it put the mouse to sleep
    idle_set = None  # the sleep time, if this command saved one

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)

    def set_report(self, body):
        fcntl.ioctl(self.fd, SET, bytearray(b"\0" + body))

    def get_report(self):
        buf = bytearray(LEN)
        fcntl.ioctl(self.fd, GET, buf)
        return bytes(buf[1:])

def transact(dev, cls, cmd, size, args=b""):
    """Send once per attempt, then poll for the reply. A stale reply to an earlier command isn't taken for this one.
    Every write sets a value, so sending one again is safe, even if the first one landed."""
    body = report(cls, cmd, size, args)
    seen = []
    sends, limit = 0, ATTEMPTS
    while sends < limit:
        sends += 1
        limit = ATTEMPTS  # only a status 3 on this send earns the longer limit
        dev.set_report(body)
        for i in range(20):
            time.sleep(0.005 * (i + 1))
            r = dev.get_report()
            header = (r[1], r[6], r[7]) == (TXN, cls, cmd)
            if not header or r[0] in (0, 1):  # not started, or busy
                continue
            if r[0] in (3, 4):  # 3 is usually the dongle timing out on the radio link, 4 is it losing the mouse: give it a moment and send again
                seen.append(r[0])
                if r[0] == 3:
                    limit = LINK_ATTEMPTS
                time.sleep(0.1)
                break
            if r[0] == 2:
                if (cls, cmd) == (2, 0x8c) and r[8:10] != args[:2]:  # the reply's third byte isn't the layer, see read_buttons
                    continue
                return bytes(r[8:8 + size])
            raise Err(STATUS.get(r[0], f"Unknown response {r[0]} from the mouse."))
    if seen == [3] * LINK_ATTEMPTS:
        raise Err(STATUS[3])
    raise (Asleep if seen == [4] * ATTEMPTS else Err)(STATUS[4])

def read_rec(dev, bank, bid, layer=0):
    return transact(dev, 2, 0x8c, 10, bytes([bank, bid, layer]))[3:10]

def write_rec(dev, bank, bid, layer, rec):
    if bid not in CONTROLS or bank not in (STORED, ACTIVE) or layer not in (0, 1) or len(rec) != 7:
        raise ValueError(f"refusing to write bank {bank} button {bid:#x} layer {layer}")
    transact(dev, 2, 0x0c, 10, bytes([bank, bid, layer]) + bytes(rec))

def put(dev, bid, layer, rec):
    """Write stored and active views, skipping any that already match, and verify anything written. A view read after
    the last write to this button is already known, so only a written view is read back, plus the stored view if the
    active one was written after it. Always ends on the active view: the next read may be this button's other layer,
    on the stored view, and see read_buttons for why those two must never be back to back."""
    wrote_stored = read_rec(dev, STORED, bid, layer) != rec
    if wrote_stored:
        write_rec(dev, STORED, bid, layer, rec)
    saved = not wrote_stored or read_rec(dev, STORED, bid, layer) == rec
    if read_rec(dev, ACTIVE, bid, layer) != rec:
        write_rec(dev, ACTIVE, bid, layer, rec)
        saved = (read_rec(dev, STORED, bid, layer), read_rec(dev, ACTIVE, bid, layer)) == (rec, rec)
    if not saved:
        raise Err(f"{CONTROLS[bid]} didn't save: the mouse read back something else.")

def read_buttons(dev):
    """One whole layer, then the other. A reply doesn't say which layer it's for, so a stale reply can only be
    told apart by its button: the same button's two layers must never be read back to back."""
    return {(b, l): read_rec(dev, STORED, b, l) for l in (0, 1) for b in CONTROLS}

def put_many(dev, changes, records=None):
    """All or nothing: on failure, every record touched goes back to what it was. Pass records if they've been read already."""
    records = records or read_buttons(dev)
    get_dpi(dev)  # read_buttons ended on the last button, which may be the next one read, on its other layer
    before = {(b, l): records[(b, l)] for b, l, _ in changes}
    try:
        for b, l, rec in changes:
            put(dev, b, l, rec)
    except Exception as e:
        undo(e, [(CONTROLS[b], functools.partial(put, dev, b, l, rec)) for (b, l), rec in before.items()])

def commit(dev, changes, records):
    """put_many, then returns the records as they now are, so nothing needs reading again."""
    put_many(dev, changes, records)
    return {**records, **{(b, l): rec for b, l, rec in changes}}

def describe(rec):
    k = rec[0]
    if k == 0x02:
        usage, mods = rec[3], rec[2]
        name = KEY_NAMES.get(usage, f"Key {usage:#04x}")
        held = [m.capitalize() for m, bit in MODS.items() if mods & bit]
        return {"kind": "key", "key": KEY_BY_USAGE.get(usage, ""),
                "mods": [m for m, bit in MODS.items() if mods & bit], "label": "+".join(held + [name])}
    if k == 0x01:
        return {"kind": "mouse", "value": MOUSE_BY_CODE.get(rec[2], ""), "label": MOUSE_NAMES.get(rec[2], "Mouse")}
    if k == 0x06:
        return {"kind": "dpi", "value": {1: "up", 2: "down"}.get(rec[2], ""), "label": {1: "DPI up", 2: "DPI down"}.get(rec[2], "DPI")}
    if k == 0x0a:
        usage = rec[2] << 8 | rec[3]
        return {"kind": "media", "value": MEDIA_BY_USAGE.get(usage, ""), "label": MEDIA_NAMES.get(usage, "Media")}
    if k == 0x0c:
        return {"kind": "hypershift", "label": "Hypershift"}
    if k == 0x00:
        return {"kind": "disabled", "label": "Disabled"}
    if k == 0x0e:
        return {"kind": "factory", "label": MOUSE_NAMES.get(rec[2], "Mouse")}
    return {"kind": "custom", "label": "Custom " + rec.hex(" ")}

def encode(action, bid):
    if not isinstance(action, dict):
        raise Err(f"Unknown action: {json.dumps(action)}")
    kind = action.get("kind")
    if kind == "key":
        if not isinstance(action.get("key"), str) or action["key"] not in KEYS:
            raise Err(f"Unknown key: {action.get('key')}")
        mods = 0
        for m in action.get("mods", []):
            if not isinstance(m, str) or m not in MODS:
                raise Err(f"Unknown modifier: {m}")
            mods |= MODS[m]
        return key_rec(KEYS[action["key"]], mods)
    if kind == "mouse" and action.get("value") in MOUSE:
        return bytes([0x01, 1, MOUSE[action["value"]], 0, 0, 0, 0])
    if kind == "dpi" and action.get("value") in ("up", "down"):
        return bytes([0x06, 1, 1 if action["value"] == "up" else 2, 0, 0, 0, 0])
    if kind == "media" and action.get("value") in MEDIA:
        u = MEDIA[action["value"]]
        return bytes([0x0a, 2, u >> 8, u & 255, 0, 0, 0])
    if kind == "hypershift":
        return HYPERSHIFT
    if kind == "disabled":
        return DISABLED
    if kind == "factory":
        return FACTORY[bid]
    raise Err(f"Unknown action: {json.dumps(action)}")

def normal_changes(normal, shifted, chosen, bid, rec):
    """What setting a button on the normal layer writes. A matching entry the user set isn't a mirror, it just looks like one.
    Only side buttons have a Hypershift entry of their own: any other can't be edited there, so it follows the normal layer."""
    if (rec == HYPERSHIFT or normal[bid] == HYPERSHIFT or bid not in SIDE_BUTTONS
            or (shifted[bid] == normal[bid] and bid not in chosen)):
        return [(bid, 0, rec), (bid, 1, rec)]  # the key stays the key while held, and a mirrored entry stays mirrored
    return [(bid, 0, rec)]

def set_button(dev, bid, layer, action):
    if bid not in CONTROLS:
        raise Err("That button can't be edited.")
    if layer not in (0, 1):
        raise Err("Unknown layer.")
    if layer == 1 and bid not in SIDE_BUTTONS:
        raise Err("Only the side buttons can have a Hypershift action.")
    rec = encode(action, bid)
    if layer == 0 and rec == HYPERSHIFT and bid not in KEY_BUTTONS:
        raise Err("Only " + " and ".join(CONTROLS[b] for b in sorted(KEY_BUTTONS, key=list(CONTROLS).index)) + " can be the Hypershift key.")
    records = read_buttons(dev)
    normal = {b: records[(b, 0)] for b in CONTROLS}
    shifted = {b: records[(b, 1)] for b in CONTROLS}
    has_key = HYPERSHIFT in normal.values()
    if layer == 1:
        if not has_key:
            raise Err("Assign a Hypershift key on the normal layer first.")
        if normal[bid] == HYPERSHIFT:
            raise Err("That button is the Hypershift key. Change it on the normal layer.")
        if rec == HYPERSHIFT:
            raise Err("Assign the Hypershift key on the normal layer.")
        records = commit(dev, [(bid, 1, rec)], records)
        set_chosen(get_chosen() | {bid})
        return records
    chosen = get_chosen()
    changes = normal_changes(normal, shifted, chosen, bid, rec)
    if rec == HYPERSHIFT and not has_key:  # first key: unassigned buttons behave the same as normal, but anything customized is kept
        changes += [(b, 1, normal[b]) for b in CONTROLS if b != bid and shifted[b] != normal[b]
                    and shifted[b] in (DISABLED, HYPERSHIFT, FACTORY[b]) and b not in chosen]
    records = commit(dev, changes, records)
    forget_chosen(changes)
    return records

def get_stages(dev):
    a = transact(dev, 4, 0x86, 0x26, b"\x01")
    count = a[2]
    return [int.from_bytes(a[4 + 7 * i:6 + 7 * i], "big") for i in range(count)], a[1]

def write_stages(dev, dpis, active):
    body = bytes([1, active, len(dpis)])
    for i, d in enumerate(dpis):
        body += bytes([i + 1]) + (d.to_bytes(2, "big") * STAGE_COPIES).ljust(6, b"\0")
    transact(dev, 4, 0x06, 0x26, body.ljust(0x26, b"\0"))

def get_dpi(dev):
    a = transact(dev, 4, 0x85, 7, b"\x01")
    return int.from_bytes(a[1:3], "big")

def write_dpi(dev, d):
    transact(dev, 4, 0x05, 7, b"\x01" + d.to_bytes(2, "big") * 2 + b"\0\0")

def put_stages(dev, dpis, active, dpi):
    write_stages(dev, dpis, active)
    write_dpi(dev, dpi)
    if get_stages(dev) != (dpis, active) or get_dpi(dev) != dpi:
        raise Err("DPI didn't save: the mouse read back something else.")

def set_dpi(dev, dpis, active):
    """active None saves only the levels, so a custom DPI that isn't one of them stays as it is."""
    if len(dpis) != 5 or any(not 100 <= d <= 30000 for d in dpis):
        raise Err("DPI levels must be five values from 100 to 30000.")
    if active is not None and not 1 <= active <= 5:
        raise Err("The active level must be 1 to 5.")
    old_stages, old_active = get_stages(dev)
    old_dpi = get_dpi(dev)
    try:
        if active is None:
            put_stages(dev, dpis, old_active, old_dpi)
        else:
            put_stages(dev, dpis, active, dpis[active - 1])
    except Exception as e:
        undo(e, [("DPI", functools.partial(put_stages, dev, old_stages, old_active, old_dpi))])

def put_dpi(dev, d):
    write_dpi(dev, d)
    if get_dpi(dev) != d:
        raise Err("DPI didn't save: the mouse read back something else.")

def set_current_dpi(dev, d):
    """Sets the DPI directly. The levels the top buttons step through aren't touched."""
    if not 100 <= d <= 30000:
        raise Err("DPI must be from 100 to 30000.")
    old = get_dpi(dev)
    try:
        put_dpi(dev, d)
    except Exception as e:
        undo(e, [("DPI", functools.partial(put_dpi, dev, old))])

def read_poll(dev):
    return transact(dev, 0, 0x85, 1)[0]

def get_poll(dev):
    v = read_poll(dev)
    return next((hz for hz, c in POLL.items() if c == v), 0)

def put_poll(dev, code):
    transact(dev, 0, 0x05, 1, bytes([code]))
    if read_poll(dev) != code:
        raise Err("Polling rate didn't save.")

def set_poll(dev, hz):
    if hz not in POLL:
        raise Err("Polling rate must be 125, 500 or 1000 Hz.")
    old = read_poll(dev)
    try:
        put_poll(dev, POLL[hz])
    except Exception as e:
        undo(e, [("polling rate", functools.partial(put_poll, dev, old))])

def get_idle(dev):
    return int.from_bytes(transact(dev, 7, 0x83, 2)[:2], "big")

def put_idle(dev, seconds, old=0):
    """old: the time this replaces. Lowering it below the time since the last movement can put the mouse to sleep
    at once, so a sleeping mouse at the read-back means it saved. Nothing can be written after this, so it goes last."""
    transact(dev, 7, 0x03, 2, seconds.to_bytes(2, "big"))
    try:
        saved = get_idle(dev)
    except Asleep:
        if seconds >= old:
            raise
        dev.slept = seconds
        return
    if saved != seconds:
        raise Err("Sleep timer didn't save.")
    dev.idle_set = seconds

def set_idle(dev, seconds):
    if not 60 <= seconds <= 900:
        raise Err("Sleep timer must be 60 to 900 seconds.")
    old = get_idle(dev)
    try:
        put_idle(dev, seconds, old)
    except Exception as e:
        undo(e, [("sleep timer", functools.partial(put_idle, dev, old))])

def apply_preset(dev, name):
    if name == "factory":
        changes = [(b, l, FACTORY[b]) for b in CONTROLS for l in (0, 1)]
        records = apply_all(dev, changes, FACTORY_DPI, FACTORY_ACTIVE, FACTORY_DPI[FACTORY_ACTIVE - 1], FACTORY_POLL, FACTORY_IDLE)
        set_loaded("")
        set_chosen(set())
        return records
    if name != "wow":
        raise Err(f"Unknown preset: {name}")
    records = read_buttons(dev)  # Ctrl+1..6 on sides 1-6; other buttons don't change
    normal, shifted = ({b: records[(b, l)] for b in CONTROLS} for l in (0, 1))
    chosen = get_chosen()
    changes = []
    for first in (0x40, 0x50):
        for i in range(6):
            if first + i in CONTROLS:
                changes += normal_changes(normal, shifted, chosen, first + i, key_rec(0x1e + i, MODS["ctrl"]))
    records = commit(dev, changes, records)
    set_loaded("")
    forget_chosen(changes)
    return records

def layouts_path():
    return os.environ.get("OMANAGA_LAYOUTS") or os.path.expanduser("~/.local/share/omanaga/layouts.json")

def prepare_data_folder():
    """Moves the folder this plugin used under its old name, Naga Control, once, and closes up anything older versions
    left readable by other accounts. Skipped for a folder chosen by OMANAGA_LAYOUTS. Nothing here is worth failing a
    command over: a folder that can't be moved is tried again next time, and a save reports its own errors."""
    if os.environ.get("OMANAGA_LAYOUTS"):
        return
    old, new = (os.path.expanduser(f"~/.local/share/{name}") for name in ("naga-control", "omanaga"))
    with contextlib.suppress(OSError):
        if os.path.isdir(old) and not os.path.lexists(new):
            os.rename(old, new)
    for folder in (old, new):
        with contextlib.suppress(OSError):
            os.chmod(folder, 0o700)
            for entry in os.scandir(folder):
                if entry.is_file(follow_symlinks=False):
                    os.chmod(entry.path, 0o600)

def read_json(path):
    with open(path) as f:
        text = f.read(MAX_DATA_FILE + 1)
    if len(text) > MAX_DATA_FILE:
        raise ValueError("too large")
    return json.loads(text)

def layout_model(layout):
    """Layouts saved before there were two models are the HyperSpeed's."""
    return layout.get("model", "hyperspeed") if isinstance(layout, dict) else "hyperspeed"

def model_layouts(data):
    """This model's layouts. The button IDs differ between models, so one model's layout can't load on another."""
    return {n: l for n, l in data.items() if layout_model(l) == MODEL}

def layout_names():
    return sorted(model_layouts(load_layouts()), key=str.lower)

def load_layouts():
    try:
        data = read_json(layouts_path())
    except FileNotFoundError:
        return {}
    except ValueError:
        raise Err("The saved layouts file is damaged, so it was left alone.")
    except OSError as e:
        raise Err(f"Couldn't read the saved layouts file: {e.strerror or e}")
    if not isinstance(data, dict):
        raise Err("The saved layouts file is damaged, so it was left alone.")
    return data

def write_json(path, data):
    """Owner-only from the moment it exists: mkstemp creates the temp file 0600, and the rename keeps that."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise

def store_layouts(data):
    if len(json.dumps(data, indent=1, sort_keys=True)) > MAX_DATA_FILE:
        raise Err("There are too many saved layouts. Delete some first.")
    try:
        write_json(layouts_path(), data)
    except OSError as e:
        raise Err(f"Couldn't save the layouts file: {e.strerror or e}")

def state_path():
    return os.path.join(os.path.dirname(layouts_path()), "state.json")

def read_state():
    """It only holds notes about the mouse, so anything wrong with its file just means no notes."""
    try:
        data = read_json(state_path())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

def update_state(**values):
    try:
        write_json(state_path(), {**read_state(), **values})
    except OSError:
        pass  # the settings are on the mouse either way, so a missing note isn't worth failing over

def skey(name):
    """Notes are kept per model. The HyperSpeed keeps the names it had before there were two."""
    return name if MODEL == "hyperspeed" else f"{MODEL}.{name}"

def get_loaded():
    """The name of the layout last loaded."""
    name = read_state().get(skey("loaded"))
    return name if isinstance(name, str) else ""

def set_loaded(name):
    update_state(**{skey("loaded"): name})

def get_chosen():
    """The buttons whose Hypershift entry the user set. The mouse can't tell a deliberate Disabled from one never set."""
    chosen = read_state().get(skey("chosen"))
    return {b for b in CONTROLS if b in chosen} if isinstance(chosen, list) else set()

def set_chosen(chosen):
    if chosen != get_chosen():
        update_state(**{skey("chosen"): sorted(chosen)})

def forget_chosen(changes):
    """A normal-layer change that also wrote the Hypershift entry leaves it following the normal layer."""
    set_chosen(get_chosen() - {b for b, l, _ in changes if l == 1})

def layout_of(records, stages, active, dpi, poll, idle):
    layout = {"buttons": {f"{b:#04x}": [records[(b, 0)].hex(), records[(b, 1)].hex()] for b in CONTROLS},
              "stages": stages, "activeStage": active, "dpi": dpi, "poll": poll, "idle": idle}
    if MODEL != "hyperspeed":  # so a HyperSpeed layout saved before there were two stays byte for byte the same
        layout["model"] = MODEL
    return layout

def snapshot(dev, records=None):
    """Everything a layout restores, read from the mouse."""
    stages, active = get_stages(dev)
    return layout_of(records or read_buttons(dev), stages, active, get_dpi(dev), get_poll(dev), get_idle(dev))

def check_layout(s):
    """Reject anything malformed before the first write, so a bad file can't half-apply."""
    try:
        changes = []
        for b in CONTROLS:
            normal, shifted = s["buttons"][f"{b:#04x}"]
            changes += [(b, 0, bytes.fromhex(normal)), (b, 1, bytes.fromhex(shifted))]
        ok = (all(len(r) == 7 for _, _, r in changes) and len(s["stages"]) == 5
              and all(type(v) is int for v in s["stages"] + [s["dpi"], s["activeStage"], s["poll"], s["idle"]])
              and all(100 <= d <= 30000 for d in s["stages"] + [s["dpi"]])
              and s["activeStage"] in range(1, 6) and s["poll"] in POLL and 60 <= s["idle"] <= 900
              and all(b in CONTROLS for b in s.get("chosen", [])))
    except (KeyError, TypeError, ValueError, AttributeError):
        ok = False
    if not ok:
        raise Err("That saved layout is damaged, so nothing was changed.")
    return changes

def apply_all(dev, changes, stages, active, dpi, poll, idle):
    """All or nothing across buttons, DPI, polling and sleep. Rollback uses raw values, because the mouse
    can be in a state a layout can't hold, like an unknown polling rate. DPI and polling go back first, since undo stops after 3 failures, and sleep last, as in put_idle."""
    records = read_buttons(dev)
    old_stages, old_active = get_stages(dev)
    old_dpi, old_poll, old_idle = get_dpi(dev), read_poll(dev), get_idle(dev)
    now = commit(dev, changes, records)  # undoes itself if it fails, and nothing else has changed yet
    try:
        put_stages(dev, stages, active, dpi)
        put_poll(dev, POLL[poll])
        put_idle(dev, idle, old_idle)
    except Exception as e:
        undo(e, [("DPI", functools.partial(put_stages, dev, old_stages, old_active, old_dpi)),
                 ("polling rate", functools.partial(put_poll, dev, old_poll))]
             + [(CONTROLS[b], functools.partial(put, dev, b, l, records[(b, l)])) for b, l, _ in changes]
             + [("sleep timer", functools.partial(put_idle, dev, old_idle))])
    return now

def apply_snapshot(dev, s):
    return apply_all(dev, check_layout(s), s["stages"], s["activeStage"], s["dpi"], s["poll"], s["idle"])

def save_layout(dev, name):
    name = name.strip()
    if not name or len(name) > 40:
        raise Err("Layout names must be 1 to 40 characters.")
    data = load_layouts()
    if name in data and layout_model(data[name]) != MODEL:
        other = MODELS.get(layout_model(data[name]), {}).get("name", "another mouse")
        raise Err(f"There's already a layout called {name} for the {other}. Pick another name.")
    records = read_buttons(dev)
    data[name] = {**snapshot(dev, records), "chosen": sorted(get_chosen())}
    try:
        check_layout(data[name])
    except Err:
        raise Err("The mouse has settings this panel can't load back, like an unknown polling rate, so the layout wasn't saved.")
    store_layouts(data)
    return records

def apply_layout(dev, name):
    data = model_layouts(load_layouts())
    if name not in data:
        raise Err(f"No saved layout called {name}.")
    records = apply_snapshot(dev, data[name])
    set_loaded(name)
    set_chosen(set(data[name].get("chosen", [])))  # layouts saved before this existed have none
    return records

def delete_layout(name):
    data = load_layouts()
    if name not in model_layouts(data):
        raise Err(f"No saved layout called {name}.")
    del data[name]
    store_layouts(data)
    if get_loaded() == name:
        set_loaded("")

def get_battery(dev):
    return round(transact(dev, 7, 0x80, 2)[1] * 100 / 255)

def battery_alert(level):
    """True only the first time the battery is low in a given drop. Kept in state.json, under the mouse lock, because
    the panel runs once per screen and restarts with the shell, and all of those together should alert once."""
    alerted = read_state().get(skey("batteryAlerted")) is True
    if level > BATTERY_REARM and alerted:
        update_state(**{skey("batteryAlerted"): False})
    elif level <= LOW_BATTERY and not alerted:
        update_state(**{skey("batteryAlerted"): True})
        return True
    return False

def dpi_level(stages, active, dpi):
    """The level the DPI is on, or 0 if it's a custom value. If two levels hold the same DPI, the active one wins."""
    levels = [i + 1 for i, d in enumerate(stages) if d == dpi]
    return active if active in levels else levels[0] if levels else 0

def get_state(dev, records=None):
    """records: the button records if this command has already read them."""
    stages, active = get_stages(dev)
    fw = transact(dev, 0, 0x81, 2)
    state = {"ok": True, "connected": True, "permission": True, "firmware": f"{fw[0]}.{fw[1]}", "battery": get_battery(dev),
             "dpi": get_dpi(dev), "stages": stages, "activeStage": active, "poll": get_poll(dev), "idle": get_idle(dev),
             "keys": list(KEYS), "buttons": []}
    records = records or read_buttons(dev)
    for bid, name in CONTROLS.items():
        state["buttons"].append({"id": bid, "name": name, "normal": describe(records[(bid, 0)]), "hypershift": describe(records[(bid, 1)]),
                                 "canBeKey": bid in KEY_BUTTONS, "layerEditable": bid in SIDE_BUTTONS,
                                 "plate": next((p for p, ids in PLATE_BUTTONS.items() if bid in ids), 0)})
    state.update(mouse_info())
    state["dpiLevel"] = dpi_level(stages, active, state["dpi"])
    state["hasHypershift"] = any(b["normal"]["kind"] == "hypershift" for b in state["buttons"])
    try:
        saved = model_layouts(load_layouts())
        current = layout_of(records, stages, active, state["dpi"], state["poll"], state["idle"])
        state["layouts"] = sorted(saved, key=str.lower)
        loaded = get_loaded()
        matches = loaded in saved and {k: v for k, v in saved[loaded].items() if k != "chosen"} == current
        state["loadedLayout"] = loaded if matches else ""  # nothing once the mouse has changed
    except Err as e:
        state["layouts"], state["loadedLayout"], state["layoutsError"] = [], "", str(e)
    return state

def control_nodes():
    """Every supported receiver's control interface, as {model: node}, the first of each model found."""
    found = {}
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(d + "/device/uevent") as f:
                ue = f.read()
        except OSError:  # unplugged while looking
            continue
        if not os.path.realpath(d + "/device").split("/")[-2].endswith(":1.0"):
            continue
        for key, m in MODELS.items():
            if key not in found and any(f"HID_ID=0003:{VID:08X}:{pid:08X}" in ue for pid in m["pids"]):
                found[key] = "/dev/" + os.path.basename(d)
    return found

PRESENT = []  # the models plugged in, in MODELS order, set by talk

def chosen_mouse(present):
    """The mouse picked last in the panel if it's plugged in, otherwise the first one that is."""
    picked = read_state().get("mouse")
    return picked if picked in present else next((k for k in MODELS if k in present), None)

def mouse_info():
    m = MODELS[MODEL]
    return {"mouse": MODEL, "mouseName": m["name"], "batteryNote": m["battery"],
            "mice": [{"id": k, "name": MODELS[k]["name"]} for k in PRESENT],
            "plates": [{"buttons": n, "name": label} for n, label in m["plates"].items()]}

def run(dev, argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "battery":
        level = get_battery(dev)
        return {"ok": True, "connected": True, "permission": True, "battery": level, "alert": battery_alert(level), **mouse_info()}
    if cmd in ("state", "select-mouse"):
        return get_state(dev)
    commands = {  # the ones that read the button records return them, so the refresh needn't read them again
        "set-button": lambda a: set_button(dev, int(a[0], 0), int(a[1]), json.loads(a[2])),
        "set-dpi": lambda a: set_dpi(dev, [int(x) for x in a[0].split(",")], None if a[1] == "keep" else int(a[1])),
        "set-current-dpi": lambda a: set_current_dpi(dev, int(a[0])),
        "set-poll": lambda a: set_poll(dev, int(a[0])),
        "set-idle": lambda a: set_idle(dev, int(a[0])),
        "save-layout": lambda a: save_layout(dev, a[0]),
        "apply-layout": lambda a: apply_layout(dev, a[0]),
        "delete-layout": lambda a: delete_layout(a[0]),
        "preset": lambda a: apply_preset(dev, a[0]),
    }
    if cmd not in commands:
        raise Err(f"Unknown command: {cmd}")
    records = commands[cmd](args)
    if dev.slept is not None:
        return {"ok": True, "connected": True, "permission": True, "idle": dev.slept, "warning": SLEPT}
    try:
        return get_state(dev, records)
    except (Err, OSError) as e:  # the command itself worked, so don't report it as failed
        asleep = isinstance(e, Asleep) and cmd != "delete-layout"  # a delete didn't save anything
        answer = {"ok": True, "connected": True, "permission": True,
                  "warning": ASLEEP_AT_REFRESH if asleep else "Done, but the mouse stopped answering before this panel could refresh."}
        if dev.idle_set is not None:
            answer["idle"] = dev.idle_set
        if cmd in ("save-layout", "delete-layout"):
            with contextlib.suppress(Err):
                answer["layouts"] = layout_names()
        return answer

def lock_owner():
    """tools/probe.py runs under sudo and has to land on the same file as the panel, so root follows SUDO_UID."""
    sudo_uid = os.environ.get("SUDO_UID")
    return int(sudo_uid) if sudo_uid and os.geteuid() == 0 else os.getuid()

def lock_path():
    """Only in the owner's private runtime folder, never a shared one like /tmp where anyone can plant the file first."""
    uid = lock_owner()
    candidates = [os.environ.get("XDG_RUNTIME_DIR")] if uid == os.getuid() else []
    for run_dir in candidates + [f"/run/user/{uid}"]:
        if run_dir and os.path.isdir(run_dir):
            return os.path.join(run_dir, f"omanaga-{uid}.lock")
    raise Err(f"Can't find a private folder for the mouse lock: XDG_RUNTIME_DIR and /run/user/{uid} are both missing.")

@contextlib.contextmanager
def mouse_lock():
    """One program talks to the mouse at a time, and waits a bounded time for its turn. Opened read-only, which flock
    allows, so an existing file is never truncated. It must be a regular file its owner made, never reached through a
    link. Root never creates it: with no lock file the panel isn't running, so a sudo tool carries on with a warning."""
    path, owner = lock_path(), lock_owner()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            fd = None if owner != os.getuid() else os.open(path, flags | os.O_CREAT, 0o600)
    except OSError as e:
        raise Err(f"Couldn't open the mouse lock {path}: {e.strerror or e}")
    if fd is None:
        print(f"No lock file at {path}, so continuing without one.", file=sys.stderr)
        yield
        return
    try:
        info = os.fstat(fd)
        if info.st_uid != owner or not stat.S_ISREG(info.st_mode):
            raise Err(f"The mouse lock {path} isn't a file owned by user {owner}, so it wasn't used.")
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Err("Another program is still using the mouse. Try again in a moment.")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)

def read_action():
    """A button's action comes over stdin, not the command line, which other accounts can read. The panel writes it
    as soon as the program starts, and an action is far smaller than a pipe's atomic write, so one read gets it all."""
    if not select.select([0], [], [], INPUT_WAIT)[0]:
        raise Err("The panel didn't send the button's new setting.")
    return os.read(0, INPUT_MAX).decode()

def main(argv):
    """Always answers with JSON: an exception that gets out of here reaches the panel as empty output."""
    try:
        if argv[0] == "set-button" and len(argv) == 3:
            argv = argv + [read_action()]
        with mouse_lock():
            prepare_data_folder()
            return talk(argv)
    except Err as e:
        return {"ok": False, "connected": True, "permission": True, "error": str(e)}
    except (IndexError, ValueError, TypeError, AttributeError, OverflowError) as e:
        return {"ok": False, "connected": True, "permission": True, "error": f"Bad request to the mouse backend: {argv[0]}: {e}"}
    except Exception as e:
        return {"ok": False, "connected": True, "permission": True, "error": f"Something unexpected went wrong: {e}"}

def without_mouse(argv, answer):
    """Saved layouts live on this computer, so listing and deleting them don't need the mouse."""
    answer.update(mouse_info())
    try:
        if argv[0] == "delete-layout":
            delete_layout(argv[1])
            answer["ok"] = True
            del answer["error"]
        answer["layouts"] = layout_names()
    except Err as e:
        answer["error"] = str(e)
    return answer

def talk(argv):
    nodes = control_nodes()
    PRESENT[:] = [k for k in MODELS if k in nodes]
    if argv[0] == "select-mouse":
        if len(argv) != 2 or argv[1] not in MODELS:
            raise Err("Unknown mouse.")
        update_state(mouse=argv[1])
    key = chosen_mouse(nodes)
    last = read_state().get("mouse")  # with nothing plugged in, saved layouts are listed for the mouse used last
    use_model(key or (last if last in MODELS else "hyperspeed"))
    node = nodes.get(key)
    no_dongle = {"ok": False, "connected": False, "permission": True, "error": "Mouse receiver not found."}
    if not node:
        return without_mouse(argv, no_dongle)
    try:
        dev = Hid(node)
    except PermissionError:
        return without_mouse(argv, {"ok": False, "connected": True, "permission": False, "error": "No permission to talk to the mouse yet."})
    except OSError:  # unplugged while looking
        return without_mouse(argv, no_dongle)
    try:
        return run(dev, argv)
    except (Err, OSError) as e:  # which mice there are goes along, so the panel can switch away from one that's asleep
        return {"ok": False, "connected": True, "permission": True, "error": error_text(e), **mouse_info()}

if __name__ == "__main__":
    signal.alarm(MAX_RUN)  # the default action ends the program, which the panel treats as no answer
    print(json.dumps(main(sys.argv[1:] or ["state"])))
