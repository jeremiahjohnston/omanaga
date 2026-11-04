#!/usr/bin/env python3
"""Read-only snapshot of Razer mice: settings, button records, then a live key watch.
Sends only read commands (command id bit 0x80 set); anything else raises before it reaches the device."""
import fcntl, glob, os, select, struct, sys, time

sys.dont_write_bytecode = True  # this runs under sudo, so don't leave root-owned cache files in the repo
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from naga_ctl import Err, mouse_lock

VID, TXN, LEN = 0x1532, 0x1f, 91  # 91 = report id byte + 90-byte Razer report
PIDS = {0x00b4: "new Naga V2 HyperSpeed", 0x00a4: "old mouse via Dock Pro"}
GET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x07
SET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x06
STATUS = {1: "busy", 2: "ok", 3: "fail", 4: "timeout", 5: "unsupported"}
BUTTONS = [("left", 0x01), ("right", 0x02), ("middle", 0x03), ("wheel_up", 0x09), ("wheel_down", 0x0a),
           ("top_front", 0x0b), ("top_back", 0x0c), ("mouse4", 0x04), ("mouse5", 0x05),
           ("tilt_left", 0x34), ("tilt_right", 0x35)] + [(f"side_{i+1}", 0x40 + i) for i in range(12)]

def read_cmd(fd, cls, cmd, size, args=b""):
    if not cmd & 0x80:
        raise ValueError(f"refusing non-read command {cls:02x}:{cmd:02x}")
    body = bytearray(90)
    body[1], body[5], body[6], body[7] = TXN, size, cls, cmd
    body[8:8 + len(args)] = args
    c = 0
    for b in body[2:88]:
        c ^= b
    body[88] = c
    for _ in range(3):
        fcntl.ioctl(fd, SET, bytearray(b"\0" + bytes(body)))
        for i in range(20):
            time.sleep(0.005 * (i + 1))
            buf = bytearray(LEN)
            fcntl.ioctl(fd, GET, buf)
            r = buf[1:]
            if (r[6], r[7]) != (cls, cmd):
                break
            if r[0] == 1:
                continue
            return STATUS.get(r[0], hex(r[0])), bytes(r[8:8 + max(size, r[5])])
    return "no-reply", b""

def show(fd, label, cls, cmd, size, args=b""):
    try:
        st, data = read_cmd(fd, cls, cmd, size, args)
        print(f"  {label:<14} {st:<11} {data.hex(' ')}")
    except OSError as e:
        print(f"  {label:<14} error {e}")

def control_nodes():
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(d + "/device/uevent") as f:
                ue = f.read()
        except OSError:  # unplugged while looking
            continue
        for pid, name in PIDS.items():
            if f"HID_ID=0003:{VID:08X}:{pid:08X}" in ue and os.path.realpath(d + "/device").split("/")[-2].endswith(":1.0"):
                yield name, "/dev/" + os.path.basename(d)

def snapshot():
    for name, path in control_nodes():
        print(f"\n=== {name} ({path})")
        fd = os.open(path, os.O_RDWR)
        for label, cls, cmd, size, args in [
            ("mode", 0, 0x84, 2, b""), ("firmware", 0, 0x81, 2, b""), ("poll", 0, 0x85, 1, b""),
            ("dpi", 4, 0x85, 7, b"\x01"), ("dpi_stages", 4, 0x86, 0x26, b"\x01"), ("battery", 7, 0x80, 2, b""),
            ("idle", 7, 0x83, 2, b""), ("profiles", 5, 0x8a, 2, b""),
        ]:
            show(fd, label, cls, cmd, size, args)
        for prof, layer, title in [(1, 0, "stored normal"), (1, 1, "stored hypershift"), (0, 0, "active normal")]:
            print(f"  -- buttons: {title} (profile {prof}, layer {layer})")
            for label, bid in BUTTONS:
                show(fd, label, 2, 0x8c, 10, bytes([prof, bid, layer]))
        os.close(fd)

NAMES = {1: "ESC", 12: "-", 13: "=", 14: "BACKSPACE", 15: "TAB", 28: "ENTER", 29: "LCTRL", 42: "LSHIFT", 54: "RSHIFT",
         56: "LALT", 57: "SPACE", 97: "RCTRL", 100: "RALT", 125: "LMETA", 126: "RMETA", 272: "BTN_LEFT", 273: "BTN_RIGHT",
         274: "BTN_MIDDLE", 275: "BTN_SIDE", 276: "BTN_EXTRA", 277: "BTN_FORWARD", 278: "BTN_BACK"}
NAMES.update({i + 2: str((i + 1) % 10) for i in range(10)})
for start, row in ((16, "QWERTYUIOP"), (30, "ASDFGHJKL"), (44, "ZXCVBNM")):
    NAMES.update({start + i: c for i, c in enumerate(row)})
NAMES.update({59 + i: f"F{i+1}" for i in range(10)})
NAMES.update({87: "F11", 88: "F12", **{183 + i: f"F{13+i}" for i in range(12)}})

def watch(seconds):
    fds = {}
    for d in glob.glob("/sys/class/input/event*"):
        try:
            if int(open(d + "/device/id/vendor").read(), 16) == VID and int(open(d + "/device/id/product").read(), 16) in PIDS:
                pid = int(open(d + "/device/id/product").read(), 16)
                fds[os.open("/dev/input/" + os.path.basename(d), os.O_RDONLY | os.O_NONBLOCK)] = (
                    "NEW" if pid == 0x00b4 else "OLD", os.path.basename(d))
        except OSError:
            pass
    print(f"\n=== WATCH {seconds}s: press side buttons 1 to 12 in order on the NEW mouse, then on the OLD mouse")
    print("    Click into another window first. Buttons set to Ctrl+1 to Ctrl+6 act as keys, and Ctrl+4 can quit commands in this terminal.")
    sys.stdout.flush()
    end = time.time() + seconds
    while time.time() < end:
        for fd in select.select(list(fds), [], [], 0.2)[0]:
            for off in range(0, len(data := os.read(fd, 24 * 64)), 24):
                _, _, typ, code, val = struct.unpack_from("qqHHi", data, off)
                if typ == 1 and val == 1:
                    print(f"  {fds[fd][0]} {fds[fd][1]:<8} {NAMES.get(code, code)}")
                    sys.stdout.flush()

def main():
    if os.geteuid():
        sys.exit("run with sudo")
    try:
        with mouse_lock():  # the same lock as the panel, so their replies can't get mixed up
            snapshot()
        watch(int(sys.argv[1]) if len(sys.argv) > 1 else 45)
    except Err as e:
        sys.exit(str(e))
    except BrokenPipeError:  # whatever this was piped into has gone, so stop quietly
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit("Stopped: the program reading this output closed.")

if __name__ == "__main__":
    main()
