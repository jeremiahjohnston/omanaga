#!/usr/bin/env python3
"""Read-only deep probe of the Naga V2 Pro, for adding it to Omanaga. Needs sudo.

Sends only read commands (command id bit 0x80 set); anything else raises before it reaches the device.
Covers: which button IDs answer, which onboard profiles hold data, charging and lighting reads,
and an optional press test that prints what each physical button sends.

Usage: sudo python3 -I tools/probe_pro.py [watch-seconds]"""
import fcntl, glob, os, select, struct, sys, time

VID, TXN, LEN = 0x1532, 0x1f, 91
PIDS = {0x00a4: "Naga V2 Pro via Mouse Dock Pro", 0x00a7: "Naga V2 Pro wired (guess)",
        0x00a8: "Naga V2 Pro dongle (guess)", 0x00b4: "Naga V2 HyperSpeed"}
GET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x07
SET = (3 << 30) | (LEN << 16) | (ord('H') << 8) | 0x06
STATUS = {1: "busy", 2: "ok", 3: "fail", 4: "timeout", 5: "unsupported"}


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
            if (r[1], r[6], r[7]) != (TXN, cls, cmd) or r[0] == 1:
                continue
            return STATUS.get(r[0], hex(r[0])), bytes(r[8:8 + max(size, r[5])])
    return "no-reply", b""


def show(fd, label, cls, cmd, size, args=b""):
    try:
        st, data = read_cmd(fd, cls, cmd, size, args)
        print(f"  {label:<26} {st:<11} {data.hex(' ')}")
        return st, data
    except OSError as e:
        print(f"  {label:<26} error {e}")
        return "error", b""


def control_nodes():
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ue = open(d + "/device/uevent").read()
        for pid, name in PIDS.items():
            if f"HID_ID=0003:{VID:08X}:{pid:08X}" in ue and os.path.realpath(d + "/device").split("/")[-2].endswith(":1.0"):
                yield pid, name, "/dev/" + os.path.basename(d)


def probe(fd):
    print("  -- device")
    for label, cls, cmd, size, args in [
            ("serial", 0, 0x82, 0x16, b""), ("firmware", 0, 0x81, 2, b""), ("mode", 0, 0x84, 2, b""),
            ("poll", 0, 0x85, 1, b""), ("poll (extended)", 0, 0xc0, 1, b""), ("poll (extended, arg 0)", 0, 0xc0, 2, b"\x00"),
            ("dpi", 4, 0x85, 7, b"\x01"), ("dpi_stages", 4, 0x86, 0x26, b"\x01"),
            ("battery", 7, 0x80, 2, b""), ("charging", 7, 0x84, 2, b""), ("low battery threshold", 7, 0x82, 1, b""),
            ("idle", 7, 0x83, 2, b""), ("scroll mode", 2, 0x94, 2, b"\x01"), ("scroll accel", 2, 0x96, 2, b"\x01"),
            ("smart reel", 2, 0x97, 2, b"\x01")]:
        show(fd, label, cls, cmd, size, args)

    print("  -- profiles (class 5 reads)")
    for cmd in range(0x80, 0x90):
        show(fd, f"5:{cmd:02x}", 5, cmd, 0x10)

    print("  -- lighting reads (class 0f, LED zones 0-6)")
    for led in range(7):
        show(fd, f"brightness led {led}", 0x0f, 0x84, 3, bytes([1, led]))
        show(fd, f"effect led {led}", 0x0f, 0x82, 0x0c, bytes([1, led]))
    for led in (0x01, 0x04, 0x05, 0x0a, 0x0c, 0x0d):
        show(fd, f"legacy brightness 3:{led:02x}", 3, 0x83, 3, bytes([1, led]))

    print("  -- button IDs that answer (profile 1, layer 0)")
    for bid in range(0x00, 0x80):
        st, data = read_cmd(fd, 2, 0x8c, 10, bytes([1, bid, 0]))
        if st == "ok":
            print(f"  id {bid:#04x}  {data[:10].hex(' ')}")

    print("  -- side 1 (0x40) and button 0x04 across profiles 0-5, layers 0 and 1")
    for prof in range(6):
        for layer in (0, 1):
            for bid in (0x40, 0x04):
                show(fd, f"profile {prof} layer {layer} id {bid:#04x}", 2, 0x8c, 10, bytes([prof, bid, layer]))


NAMES = {1: "ESC", 12: "-", 13: "=", 29: "LCTRL", 42: "LSHIFT", 56: "LALT", 125: "LMETA", 272: "BTN_LEFT",
         273: "BTN_RIGHT", 274: "BTN_MIDDLE", 275: "BTN_SIDE", 276: "BTN_EXTRA", 277: "BTN_FORWARD", 278: "BTN_BACK",
         113: "MUTE", 114: "VOLDOWN", 115: "VOLUP", 163: "NEXT", 164: "PLAYPAUSE", 165: "PREV"}
NAMES.update({i + 2: str((i + 1) % 10) for i in range(10)})
for start, row in ((16, "QWERTYUIOP"), (30, "ASDFGHJKL"), (44, "ZXCVBNM")):
    NAMES.update({start + i: c for i, c in enumerate(row)})
NAMES.update({59 + i: f"F{i+1}" for i in range(10)})
NAMES.update({87: "F11", 88: "F12", **{183 + i: f"F{13+i}" for i in range(12)}})


def watch(seconds):
    fds = {}
    for d in glob.glob("/sys/class/input/event*"):
        try:
            pid = int(open(d + "/device/id/product").read(), 16)
            if int(open(d + "/device/id/vendor").read(), 16) == VID and pid in PIDS and pid != 0x00b4:
                fds[os.open("/dev/input/" + os.path.basename(d), os.O_RDONLY | os.O_NONBLOCK)] = os.path.basename(d)
        except OSError:
            pass
    print(f"\n=== WATCH {seconds}s on the V2 Pro: press, in order, each button behind the wheel (front then back),"
          " any button underneath, wheel tilt left then right, then side 1 to 12")
    sys.stdout.flush()
    end = time.time() + seconds
    while time.time() < end and fds:
        for fd in select.select(list(fds), [], [], 0.2)[0]:
            for off in range(0, len(data := os.read(fd, 24 * 64)), 24):
                _, _, typ, code, val = struct.unpack_from("qqHHi", data, off)
                if typ == 1 and val == 1:
                    print(f"  {fds[fd]:<8} key {NAMES.get(code, code)}")
                elif typ == 2 and code in (6, 12):
                    print(f"  {fds[fd]:<8} hwheel {val}")
                sys.stdout.flush()


if __name__ == "__main__":
    if os.geteuid():
        sys.exit("run with sudo")
    for pid, name, path in control_nodes():
        print(f"\n=== {name} ({path}, pid {pid:#06x})")
        if pid == 0x00b4:
            print("  skipped: already supported")
            continue
        fd = os.open(path, os.O_RDWR)
        probe(fd)
        os.close(fd)
    if len(sys.argv) > 1:
        watch(int(sys.argv[1]))
