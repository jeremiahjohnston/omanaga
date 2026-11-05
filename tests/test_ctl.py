import importlib.util, json, os, pwd, sys, tempfile, time, traceback
# The suite must never touch the user's files, so everything the backend could resolve points into a throwaway folder
# before it loads, and reaching the real data folder or lock file ends the run. Exits, since the backend catches errors.
UID = os.getuid()
REAL_DATA = {os.path.realpath(os.path.join(h, ".local/share", name)) for h in (os.path.expanduser("~"), pwd.getpwuid(UID).pw_dir) for name in ("omanaga", "naga-control")}
REAL_LOCKS = {os.path.join(os.path.realpath(d), f"{name}-{UID}.lock") for name in ("omanaga", "naga-ctl") for d in (os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{UID}", f"/run/user/{UID}", tempfile.gettempdir())}
def forbidden(path):
    if not isinstance(path, (str, bytes, os.PathLike)): return False  # an open file descriptor
    path = os.path.realpath(os.fsdecode(path))
    return path in REAL_LOCKS or any(path == d or path.startswith(d + os.sep) for d in REAL_DATA)
def guard(event, args):
    paths = args[:1] if event in ("open", "os.mkdir", "os.remove", "os.rmdir") else args[:2] if event == "os.rename" else ()
    for path in paths:
        if forbidden(path):
            print(f"FAILED: a test reached the user's real files: {event} {os.fsdecode(path)}", file=sys.stderr)
            traceback.print_stack(file=sys.stderr); sys.stderr.flush(); os._exit(1)
sys.addaudithook(guard)
SANDBOX = tempfile.mkdtemp(prefix="naga-test-")
os.environ.pop("SUDO_UID", None)
os.environ.update(HOME=SANDBOX, XDG_RUNTIME_DIR=SANDBOX, OMANAGA_LAYOUTS=os.path.join(SANDBOX, "layouts.json"))
s = importlib.util.spec_from_file_location("c", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "naga_ctl.py"))
c = importlib.util.module_from_spec(s); s.loader.exec_module(c)
time.sleep = lambda *_: None
assert len(REAL_DATA) >= 2 and all(forbidden(os.path.join(d, "state.json")) for d in REAL_DATA) and all(map(forbidden, REAL_LOCKS)) and not forbidden(0)
assert not any(map(forbidden, (c.layouts_path(), c.state_path(), c.lock_path())))
THIRD_BYTE_SET = {0x0b, 0x0c, *range(0x40, 0x46)}
class Fake:
    slept = idle_set = None
    @property
    def stages(self): return [int.from_bytes(s[1:3], "big") for s in self.stage_recs]
    @stages.setter
    def stages(self, dpis): self.stage_recs = [bytes([i + 1]) + d.to_bytes(2, "big") * 3 for i, d in enumerate(dpis)]
    def __init__(self, reject=None, corrupt=None, follow=True):
        self.mem = {(bk, l, b): c.FACTORY[b] for bk in (0, 1) for l in (0, 1) for b in c.CONTROLS}
        for bk in (0, 1):
            self.mem[(bk, 0, 1)] = self.mem[(bk, 1, 1)] = bytes([1, 1, 1, 0, 0, 0, 0])
        self.stages, self.active, self.dpi, self.poll, self.idle = list(c.FACTORY_DPI), 3, 1600, 1, 300
        self.writes, self.last, self.reject, self.corrupt, self.follow = [], None, reject, corrupt, follow
        self.tx, self.battery, self.stage_rejects = 0, 0xcd, 0
    def set_report(self, body):
        self.tx += 1
        assert len(body) == 90 and body[1] == 0x1f
        x = 0
        for v in body[2:88]: x ^= v
        assert x == body[88], "crc"
        r = bytearray(body); r[0] = 2; a = body[8:]; key = (body[6], body[7])
        if key == (2, 0x0c):
            bk, b, l = a[:3]; rec = bytes(a[3:10]); self.writes.append(("btn", bk, b, l, rec))
            n = len(self.writes)
            if isinstance(self.reject, int) and 0 <= n - self.reject < c.LINK_ATTEMPTS: r[0] = 3  # every attempt at that write
            else:
                self.mem[(bk, l, b)] = b"\xee" * 7 if self.corrupt == n else rec
                if self.follow and bk == 1: self.mem[(0, l, b)] = self.mem[(1, l, b)]
        elif key == (2, 0x8c):  # the third byte isn't the layer: on the real mouse it depends on the button and matches on both layers
            r[8 + 3:8 + 10] = self.mem[(a[0], a[2], a[1])]; r[10] = int(a[1] in THIRD_BYTE_SET)
        elif key == (4, 0x86):
            r[8:] = b"\0" * 82; r[8:11] = bytes([1, self.active, len(self.stage_recs)])
            r[11:11 + 7 * len(self.stage_recs)] = b"".join(self.stage_recs); del r[90:]
        elif key == (4, 0x06):
            assert a[0] == 1; self.writes.append(("stages",)); count = a[2]
            assert [a[3 + 7 * i] for i in range(count)] == list(range(1, count + 1))
            if self.reject == "stages":
                r[0] = 3; self.stage_rejects += 1
                if self.stage_rejects == c.LINK_ATTEMPTS: self.reject, self.stage_rejects = None, 0
            else:
                self.active = a[1]; self.stage_recs = [bytes(a[3 + 7 * i:10 + 7 * i]) for i in range(count)]
                if self.corrupt == "stages": self.corrupt = None; self.stage_recs[0] = b"\1" + (999).to_bytes(2, "big") * 3
        elif key == (4, 0x85): r[9:11] = self.dpi.to_bytes(2, "big")
        elif key == (4, 0x05): self.dpi = int.from_bytes(a[1:3], "big")
        elif key == (0, 0x85): r[8] = self.poll
        elif key == (0, 0x05): self.poll = a[0]
        elif key == (7, 0x83): r[8:10] = self.idle.to_bytes(2, "big")
        elif key == (7, 0x03): self.idle = int.from_bytes(a[:2], "big")
        elif key == (7, 0x80): r[9] = self.battery
        elif key == (0, 0x81): r[8:10] = b"\1\1"
        else: raise AssertionError(f"unexpected {key}")
        self.last = bytes(r)
    def get_report(self): return self.last
SIDE1, TOPF = 0x40, 0x0b
ctrl1 = {"kind": "key", "key": "1", "mods": ["ctrl"]}
def old_key(f, b):  # a Hypershift key on a button that can't be given one any more, as loaded from an old layout
    for bk in (0, 1):
        for l in (0, 1): f.mem[(bk, l, b)] = c.HYPERSHIFT
# encoding matches what's on the real mouse now, and decoding agrees
assert c.encode(ctrl1, SIDE1) == bytes.fromhex("0202011e000000")
assert c.describe(bytes.fromhex("0202011e000000"))["label"] == "Ctrl+1"
assert c.describe(c.FACTORY[SIDE1])["label"] == "1" and c.describe(c.FACTORY[TOPF])["label"] == "DPI up"
assert c.describe(c.FACTORY[0x0c])["label"] == "DPI down" and c.describe(c.FACTORY[0x34])["kind"] == "factory"
# state
f = Fake(); st = c.get_state(f)
assert st["stages"] == c.FACTORY_DPI and st["poll"] == 1000 and st["battery"] == 80 and st["idle"] == 300 and st["firmware"] == "1.1"
assert not f.writes and len(st["buttons"]) == 17 and not st["hasHypershift"]
json.dumps(st)
# single button write hits only that button, both views stay right, and repeating writes nothing
c.set_button(f, SIDE1, 0, ctrl1)
assert {w[2] for w in f.writes} == {SIDE1} and f.mem[(1, 0, SIDE1)] == f.mem[(0, 0, SIDE1)] == c.encode(ctrl1, SIDE1)
n = len(f.writes); c.set_button(f, SIDE1, 0, ctrl1); assert len(f.writes) == n
# active view that doesn't follow still gets written
f = Fake(follow=False); c.set_button(f, SIDE1, 0, ctrl1); assert f.mem[(0, 0, SIDE1)] == c.encode(ctrl1, SIDE1)
# left, right, wheel can't be written by any path
for bad in (0x01, 0x02, 0x09, 0x0a):
    try: c.set_button(Fake(), bad, 0, ctrl1); assert 0
    except c.Err: pass
    try: c.write_rec(Fake(), 1, bad, 0, bytes(7)); assert 0
    except ValueError: pass
# rollback on rejection and on bad read-back
for kw in ({"reject": 2}, {"corrupt": 2}):
    f = Fake(**kw); orig = dict(f.mem); hs = {"kind": "hypershift"}
    try: c.set_button(f, TOPF, 0, hs); assert 0, kw
    except c.Err: pass
    assert {k: v for k, v in f.mem.items() if k[0] == 1} == {k: v for k, v in orig.items() if k[0] == 1}, kw
# hypershift: layer locked until a key exists, first key mirrors the normal layer, key stays in both layers
f = Fake(); c.set_button(f, SIDE1, 0, ctrl1)
try: c.set_button(f, 0x41, 1, ctrl1); assert 0
except c.Err: pass
c.set_button(f, TOPF, 0, {"kind": "hypershift"})
assert f.mem[(1, 0, TOPF)] == f.mem[(1, 1, TOPF)] == c.HYPERSHIFT and f.mem[(1, 1, SIDE1)] == c.encode(ctrl1, SIDE1)
# layer edit touches only the layer; mirrored entries follow, customized ones don't
c.set_button(f, 0x41, 1, {"kind": "media", "value": "play"})
assert f.mem[(1, 1, 0x41)] == bytes([0x0a, 2, 0, 0xcd, 0, 0, 0]) and f.mem[(1, 0, 0x41)] == c.FACTORY[0x41]
c.set_button(f, 0x41, 0, {"kind": "key", "key": "z"}); assert f.mem[(1, 1, 0x41)][0] == 0x0a
c.set_button(f, 0x42, 0, {"kind": "key", "key": "x"}); assert f.mem[(1, 1, 0x42)] == f.mem[(1, 0, 0x42)]
for args in ((TOPF, 1, ctrl1), (0x43, 1, {"kind": "hypershift"})):
    try: c.set_button(f, *args); assert 0
    except c.Err: pass
# invalid actions write nothing
f = Fake()
for a in ({"kind": "key", "key": "nope"}, {"kind": "key", "key": "1", "mods": ["hyper"]}, {"kind": "mouse", "value": "x"}, {"kind": "boom"}):
    try: c.set_button(f, SIDE1, 0, a); assert 0
    except c.Err: pass
assert not f.writes
# DPI
f = Fake(); c.set_dpi(f, [500, 1000, 2000, 4000, 8000], 2); assert f.stages == [500, 1000, 2000, 4000, 8000] and f.active == 2 and f.dpi == 1000
for bad in (([50, 1000, 2000, 4000, 8000], 1), ([500, 1000, 2000, 4000, 30001], 1), ([500] * 4, 1), ([500] * 5, 6)):
    f = Fake()
    try: c.set_dpi(f, *bad); assert 0
    except c.Err: assert f.dpi == 1600 and not f.writes
for kw in ({"corrupt": "stages"}, {"reject": "stages"}):
    f = Fake(**kw)
    try: c.set_dpi(f, [500, 1000, 2000, 4000, 8000], 2); assert 0, kw
    except c.Err: assert f.dpi == 1600 and f.active == 3 and f.stages == c.FACTORY_DPI, kw
# direct DPI leaves the levels and the DPI buttons alone
f = Fake(); c.set_current_dpi(f, 1234); assert f.dpi == 1234 and f.stages == c.FACTORY_DPI and f.active == 3 and not f.writes
assert f.mem[(1, 0, TOPF)] == c.FACTORY[TOPF]
for bad in (99, 30001):
    try: c.set_current_dpi(Fake(), bad); assert 0
    except c.Err: pass
assert c.run(Fake(), ["set-current-dpi", "2000"])["dpi"] == 2000
# polling and sleep timer
f = Fake(); c.set_poll(f, 500); assert c.get_poll(f) == 500; c.set_idle(f, 600); assert c.get_idle(f) == 600
for fn, v in ((c.set_poll, 750), (c.set_idle, 30), (c.set_idle, 901)):
    try: fn(Fake(), v); assert 0
    except c.Err: pass
# presets
f = Fake(); c.apply_preset(f, "wow"); assert f.mem[(1, 0, 0x45)] == c.key_rec(0x23, 1) and f.mem[(1, 0, 0x46)] == c.FACTORY[0x46]
assert all(w[2] in range(0x40, 0x46) for w in f.writes)
c.set_dpi(f, [500] * 5, 1); c.set_poll(f, 125); c.apply_preset(f, "factory")
assert all(f.mem[(1, l, b)] == c.FACTORY[b] for b in c.CONTROLS for l in (0, 1)) and f.stages == c.FACTORY_DPI and f.poll == 1 and f.dpi == 1600
# factory restore leaves left click alone
assert f.mem[(1, 0, 1)] == bytes([1, 1, 1, 0, 0, 0, 0])
# run() and unknown commands
assert c.run(Fake(), ["battery"])["battery"] == 80 and c.run(Fake(), ["state"])["ok"]
try: c.run(Fake(), ["zap"]); assert 0
except c.Err: pass
# saved layouts
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "sub", "layouts.json")
f = Fake(); assert c.run(f, ["state"])["layouts"] == []
c.set_button(f, SIDE1, 0, ctrl1); c.set_current_dpi(f, 1234); c.set_poll(f, 500); c.set_idle(f, 600)
st = c.run(f, ["save-layout", " WoW "]); assert st["layouts"] == ["WoW"] and st["loadedLayout"] == ""
wow = c.snapshot(f)
c.apply_preset(f, "factory"); st = c.run(f, ["state"]); assert st["loadedLayout"] == "" and f.mem[(1, 0, SIDE1)] == c.FACTORY[SIDE1]
st = c.run(f, ["save-layout", "Desktop"]); assert st["layouts"] == ["Desktop", "WoW"] and st["loadedLayout"] == ""
st = c.run(f, ["apply-layout", "WoW"]); assert st["loadedLayout"] == "WoW" and c.snapshot(f) == wow and f.dpi == 1234 and f.poll == 2
assert f.mem[(1, 0, 1)] == bytes([1, 1, 1, 0, 0, 0, 0]), "left click untouched"
n = len(f.writes); c.run(f, ["apply-layout", "WoW"]); assert not [w for w in f.writes[n:] if w[0] == "btn"], "no button rewrites when identical"
# hypershift layer round-trips
c.set_button(f, TOPF, 0, {"kind": "hypershift"}); c.set_button(f, 0x41, 1, {"kind": "media", "value": "mute"}); c.run(f, ["save-layout", "HS"])
hs = c.snapshot(f); c.run(f, ["apply-layout", "WoW"]); assert f.mem[(1, 1, 0x41)].hex() != hs["buttons"]["0x41"][1]; c.run(f, ["apply-layout", "HS"]); assert c.snapshot(f) == hs
# a failure partway through the buttons, or after them, puts everything back
c.run(f, ["apply-layout", "WoW"]); before = c.snapshot(f); f.reject = len(f.writes) + 2
try: c.run(f, ["apply-layout", "Desktop"]); assert 0
except c.Err as e: assert "rejected" in str(e)
assert len(f.writes) >= f.reject + c.LINK_ATTEMPTS, "the rollback wrote the earlier button back"
f.reject = None; assert c.snapshot(f) == before
for kw in ({"reject": "stages"}, {"corrupt": "stages"}):
    f.__dict__.update(kw)
    try: c.run(f, ["apply-layout", "Desktop"]); assert 0, kw
    except c.Err: pass
    f.reject = f.corrupt = None; assert c.snapshot(f) == before, kw
# damaged data writes nothing
data = c.load_layouts()
bad = json.loads(json.dumps(data["WoW"])); bad["stages"][0] = 5; data["bad1"] = bad
bad = json.loads(json.dumps(data["WoW"])); bad["buttons"]["0x40"][0] = "zz"; data["bad2"] = bad
bad = json.loads(json.dumps(data["WoW"])); del bad["buttons"]["0x4b"]; data["bad3"] = bad
data["bad4"] = "nope"
for i, (k, v) in enumerate((("activeStage", 3.0), ("activeStage", True), ("poll", 1000.0), ("idle", 300.0), ("dpi", 1600.0))):
    data[f"bad_type{i}"] = {**data["WoW"], k: v}
c.store_layouts(data)
for name in ["bad1", "bad2", "bad3", "bad4"] + [f"bad_type{i}" for i in range(5)]:
    f = Fake()
    try: c.run(f, ["apply-layout", name]); assert 0, name
    except c.Err: assert not f.writes, name
# names, missing layouts, delete
for name in ("", "   ", "x" * 41):
    try: c.save_layout(Fake(), name); assert 0
    except c.Err: pass
for cmd in (["apply-layout", "nope"], ["delete-layout", "nope"]):
    try: c.run(Fake(), cmd); assert 0
    except c.Err: pass
st = c.run(Fake(), ["delete-layout", "Desktop"]); assert "Desktop" not in st["layouts"] and "WoW" in st["layouts"]
# damaged file: state still works and the file isn't overwritten
open(c.layouts_path(), "w").write("{oops"); st = c.run(Fake(), ["state"]); assert st["ok"] and st["layouts"] == [] and "damaged" in st["layoutsError"]
try: c.save_layout(Fake(), "new"); assert 0
except c.Err: assert open(c.layouts_path()).read() == "{oops"
# a failed load puts back settings a layout can't hold, and reports what actually went wrong
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
g = Fake(); g.mem[(1, 0, SIDE1)] = c.encode(ctrl1, SIDE1); c.save_layout(g, "good")
f = Fake(reject="stages"); f.poll, f.idle, f.stages, f.active = 4, 1200, [400, 800, 1600], 2; was = dict(f.mem)
try: c.apply_layout(f, "good"); assert 0
except c.Err as e: assert "rejected" in str(e), e
assert f.mem == was and (f.poll, f.idle, f.stages, f.active, f.dpi) == (4, 1200, [400, 800, 1600], 2, 1600)
# a state the panel couldn't load back isn't saved
for attr, v in (("poll", 4), ("idle", 1200), ("stages", [400, 800, 1600])):
    f = Fake(); setattr(f, attr, v); saved = c.load_layouts()
    try: c.save_layout(f, "x"); assert 0, attr
    except c.Err as e: assert "wasn't saved" in str(e) and c.load_layouts() == saved, attr
# one record that won't go back doesn't stop the others, and the error says which and keeps the first cause
sides = [(0x40 + i, 0, c.key_rec(0x04 + i)) for i in range(4)]
f = Fake(corrupt=3, reject=5); was = dict(f.mem)
try: c.put_many(f, sides); assert 0
except c.Err as e: assert "Side 3 didn't save" in str(e) and "Side 1" in str(e) and "Side 2" not in str(e), e
assert f.mem[(1, 0, 0x41)] == was[(1, 0, 0x41)] and f.mem[(1, 0, 0x42)] == was[(1, 0, 0x42)]
# a mouse that stops answering: the first cause survives and undo gives up instead of waiting on every record
class Gone(Fake):
    gone_after = None
    def set_report(self, body):
        if self.gone_after is not None and len(self.writes) >= self.gone_after:
            self.tx_gone = getattr(self, "tx_gone", 0) + 1; r = bytearray(body); r[0] = 4; self.last = bytes(r)
        else: super().set_report(body)
f = Gone(corrupt=3); f.gone_after = 4
try: c.put_many(f, sides); assert 0
except c.Err as e: assert "Side 3 didn't save" in str(e) or "didn't answer" in str(e), e; assert "may not be back" in str(e), e; assert e.__cause__ is not None
assert f.tx_gone <= 3 * 4, f.tx_gone
f = Gone(); f.gone_after = 1
try: c.apply_all(f, sides, c.FACTORY_DPI, 3, 1600, 1000, 300); assert 0
except c.Err as e: assert "may not be back" in str(e), e
# moving the key by clearing the old one first keeps the customized layer; blank and default entries still copy
f = Fake(); c.set_button(f, TOPF, 0, {"kind": "hypershift"}); c.set_button(f, 0x41, 1, {"kind": "media", "value": "mute"})
custom = f.mem[(1, 1, 0x41)]; c.set_button(f, TOPF, 0, {"kind": "dpi", "value": "up"}); assert f.mem[(1, 1, 0x41)] == custom
c.set_button(f, 0x0c, 0, {"kind": "hypershift"}); assert f.mem[(1, 1, 0x41)] == custom and f.mem[(1, 1, 0x0c)] == c.HYPERSHIFT
f = Fake(); f.mem[(1, 1, 0x42)] = c.DISABLED; f.mem[(1, 0, 0x43)] = c.encode(ctrl1, 0x43); c.set_button(f, TOPF, 0, {"kind": "hypershift"})
assert f.mem[(1, 1, 0x42)] == f.mem[(1, 0, 0x42)] and f.mem[(1, 1, 0x43)] == c.encode(ctrl1, 0x43)
# a reply still waiting from the previous read isn't taken for this one, including the same button on the other layer
class Lag(Fake):
    def __init__(self, *a, **k): super().__init__(*a, **k); self.prev = None; self.pending = False
    def set_report(self, body): self.prev = self.last; super().set_report(body); self.pending = True
    def get_report(self):
        if self.pending and self.prev is not None: self.pending = False; return self.prev
        return self.last
f = Lag(); f.mem[(1, 1, SIDE1)] = c.encode({"kind": "media", "value": "mute"}, SIDE1)
assert c.snapshot(f)["buttons"]["0x40"] == [c.FACTORY[SIDE1].hex(), f.mem[(1, 1, SIDE1)].hex()]
assert c.read_rec(f, 0, 0x41) == c.FACTORY[0x41] and c.read_rec(f, 1, 0x41) == c.FACTORY[0x41]
# each command reads the 34 button records once
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
def cost(argv, f=None):
    f = f or Fake(); f.tx = 0; c.run(f, argv); return f.tx
assert cost(["state"]) == 40  # 34 records, then stages, firmware, battery, DPI, polling, sleep
assert cost(["set-button", "64", "0", json.dumps(ctrl1)]) == 49  # two entries written, each one read back once
assert cost(["set-button", "64", "0", json.dumps(ctrl1)], Fake(follow=False)) == 55  # an active view that doesn't follow is written and verified too
assert cost(["set-poll", "500"]) == 43 and cost(["set-current-dpi", "1234"]) == 43
assert cost(["save-layout", "same"]) == 44
assert cost(["apply-layout", "same"]) == 34 + 5 + 68 + 8 + 6  # nothing differs, so each of the 68 entries is two reads, not one
assert cost(["preset", "wow"]) == 89
f = Fake(); c.apply_preset(f, "wow"); assert cost(["apply-layout", "same"], f) == 145  # 12 entries go back
# with saved layouts to compare against, state costs no more, and the comparison still works
assert cost(["state"]) == 40 and c.run(Fake(), ["state"])["loadedLayout"] == "same"
# the Ctrl+1-6 preset follows the layer rules: a Hypershift key on a side button stops being one, mirrored entries follow, custom ones stay
f = Fake(); old_key(f, SIDE1); c.apply_preset(f, "wow")
assert f.mem[(1, 0, SIDE1)] == f.mem[(1, 1, SIDE1)] == c.key_rec(0x1e, 1) and not c.get_state(f)["hasHypershift"]
f = Fake(); c.set_button(f, TOPF, 0, {"kind": "hypershift"}); c.set_button(f, 0x42, 1, {"kind": "media", "value": "mute"}); custom = f.mem[(1, 1, 0x42)]
c.apply_preset(f, "wow"); assert f.mem[(1, 1, 0x41)] == f.mem[(1, 0, 0x41)] == c.key_rec(0x1f, 1) and f.mem[(1, 1, 0x42)] == custom and f.mem[(1, 0, TOPF)] == c.HYPERSHIFT
# factory reset is all or nothing
for kw in ({"reject": "stages"}, {"corrupt": "stages"}):
    f = Fake(**kw); c.set_button(f, SIDE1, 0, ctrl1); f.poll, f.idle = 2, 600; was = dict(f.mem)
    try: c.apply_preset(f, "factory"); assert 0, kw
    except c.Err: pass
    assert f.mem == was and f.poll == 2 and f.idle == 600, kw
class PollStuck(Fake):
    "polling rate writes don't stick, the step after DPI and buttons"
    def set_report(self, body):
        keep = self.poll; super().set_report(body)
        if (body[6], body[7]) == (0, 0x05) and body[8] == 1: self.poll = keep
f = PollStuck(); c.set_button(f, SIDE1, 0, ctrl1); f.poll = 2; f.dpi = 500; c.set_dpi(f, [500] * 5, 1); was = dict(f.mem)
try: c.apply_preset(f, "factory"); assert 0
except c.Err as e: assert "Polling" in str(e)
assert f.mem == was and f.stages == [500] * 5 and f.active == 1 and f.dpi == 500 and f.poll == 2
# main always answers with JSON
real_glob, real_hid, real_node = c.glob, c.Hid, c.control_nodes
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(); os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
c.control_nodes = lambda: {"hyperspeed": "/dev/hidraw-test"}; c.Hid = lambda path: Fake()
assert c.main(["state"])["ok"] and c.main(["battery"])["battery"] == 80
for argv in (["set-button", "64"], ["set-poll"], ["set-dpi", "a,b", "1"], ["set-button", "x", "0", "{}"], ["set-button", "64", "0", "{bad"],
             ["set-button", "64", "0", "[]"], ["set-button", "64", "0", '{"kind": "key", "key": []}'], ["set-button", "64", "0", '{"kind": "key", "key": "a", "mods": [[]]}'],
             ["set-idle", "99999999999"], ["zap"], ["apply-layout"]):
    r = c.main(argv); assert r["ok"] is False and r["error"] and r["connected"], argv; json.dumps(r)
for argv in (["set-button", "64"], ["set-poll"], ["set-dpi", "a,b", "1"], ["set-button", "x", "0", "{}"], ["set-button", "64", "0", "{bad"], ["apply-layout"]):
    assert c.main(argv)["error"].startswith("Bad request"), argv
def broken(path): raise RuntimeError("boom")
c.Hid = broken; r = c.main(["state"]); assert r["ok"] is False and "boom" in r["error"]
class Err3(Fake):
    def set_report(self, body): self.tx += 1; raise OSError(19, "No such device")
c.Hid = lambda path: Err3(); r = c.main(["state"]); assert r["error"] == "Couldn't reach the mouse: No such device" and r["connected"]
c.Hid = lambda path: Fake(reject=1); r = c.main(["set-button", "64", "0", json.dumps(ctrl1)]); assert r["ok"] is False and "rejected" in r["error"]
def denied(path): raise PermissionError(13, "denied")
c.Hid = denied; r = c.main(["state"]); assert r["permission"] is False and r["connected"]
def vanished(path): raise FileNotFoundError(2, "gone")
c.Hid = vanished; r = c.main(["state"]); assert r["connected"] is False
c.control_nodes = lambda: {}; r = c.main(["state"]); assert r["connected"] is False and r["ok"] is False
# control_nodes picks the Razer receiver's control interface and survives an unplug while looking
c.Hid, c.control_nodes = real_hid, real_node
sysfs = tempfile.mkdtemp()
def hidraw(name, iface, hid):
    p = os.path.join(sysfs, "devices", iface, f"{hid}.{name}"); os.makedirs(p); open(p + "/uevent", "w").write(f"DRIVER=hid-generic\nHID_ID={hid}\n")
    d = os.path.join(sysfs, "class", name); os.makedirs(d); os.symlink(p, d + "/device")
hidraw("hidraw3", "1-2:1.1", "0003:00001532:000000B4"); hidraw("hidraw5", "1-3:1.0", "0003:0000046D:0000C52B"); hidraw("hidraw2", "1-9:1.0", "0005:00001532:000000B4")
os.makedirs(os.path.join(sysfs, "class", "hidraw1"))
class FakeGlob:
    glob = staticmethod(lambda pattern: real_glob.glob(os.path.join(sysfs, "class", "hidraw*")))
c.glob = FakeGlob
assert c.control_nodes() == {}
hidraw("hidraw4", "1-2:1.0", "0003:00001532:000000B4"); assert c.control_nodes() == {"hyperspeed": "/dev/hidraw4"}
c.glob = real_glob
# a command that worked isn't reported as failed because the refresh after it didn't, and a failed state read doesn't look like an empty layout list
class StopsAfter(Fake):
    "stops answering after the nth time it sees a command"
    def __init__(self, key, n=1): super().__init__(); self.key, self.n = key, n
    def set_report(self, body):
        if self.n == 0: r = bytearray(body); r[0] = 4; self.last = bytes(r); return
        super().set_report(body)
        if (body[6], body[7]) == self.key: self.n -= 1
f = StopsAfter((7, 0x83), 2); r = c.run(f, ["set-idle", "600"])  # the read before and the verify after
assert r == {"ok": True, "connected": True, "permission": True, "idle": 600, "warning": c.ASLEEP_AT_REFRESH} and f.idle == 600, r
f = StopsAfter((2, 0x8c))
try: c.run(f, ["state"]); assert 0
except c.Err as e: assert "didn't answer" in str(e)
# layouts file problems: anything that isn't a layouts object is left alone, and errors say it's the file, not the mouse
p = os.path.join(tempfile.mkdtemp(), "layouts.json"); os.environ["OMANAGA_LAYOUTS"] = p
open(p, "w").write('["precious"]')
try: c.save_layout(Fake(), "new"); assert 0
except c.Err as e: assert "damaged" in str(e) and open(p).read() == '["precious"]'
st = c.run(Fake(), ["state"]); assert st["ok"] and st["layouts"] == [] and "damaged" in st["layoutsError"]
os.remove(p); os.mkdir(p); st = c.run(Fake(), ["state"]); assert st["ok"] and "Couldn't read" in st["layoutsError"]; os.rmdir(p)
blocker = os.path.join(tempfile.mkdtemp(), "file"); open(blocker, "w").close(); os.environ["OMANAGA_LAYOUTS"] = os.path.join(blocker, "layouts.json")
try: c.store_layouts({}); assert 0
except c.Err as e: assert "Couldn't save" in str(e)
# a save that fails partway leaves the old file whole
os.environ["OMANAGA_LAYOUTS"] = p; c.store_layouts({"a": 1})
try: c.store_layouts({"a": 1, "b": object()}); assert 0
except TypeError: assert json.load(open(p)) == {"a": 1}
# saved files have a size limit: a bigger one is left alone, and a save can't make one the backend couldn't read back
real_max = c.MAX_DATA_FILE; c.MAX_DATA_FILE = 100; big = json.dumps({"a": "x" * 200}); keep = open(p).read()
open(p, "w").write(big)
try: c.load_layouts(); assert 0
except c.Err as e: assert "damaged" in str(e) and open(p).read() == big
open(c.state_path(), "w").write(big); assert c.read_state() == {}; os.remove(c.state_path())
open(p, "w").write(keep)
try: c.store_layouts({"a": "x" * 200}); assert 0
except c.Err as e: assert "too many" in str(e) and open(p).read() == keep
c.MAX_DATA_FILE = real_max
# saved files are owner-only under any umask, a failed save leaves no temp file behind, and a save never widens them
old_umask = os.umask(0); folder = os.path.join(tempfile.mkdtemp(), "new"); q = os.path.join(folder, "layouts.json"); os.environ["OMANAGA_LAYOUTS"] = q
c.store_layouts({"a": 1}); assert os.stat(q).st_mode & 0o777 == 0o600 and os.stat(folder).st_mode & 0o777 == 0o700
c.update_state(note=1); assert os.stat(c.state_path()).st_mode & 0o777 == 0o600
try: c.store_layouts({"b": object()}); assert 0
except TypeError: assert sorted(os.listdir(folder)) == ["layouts.json", "state.json"] and json.load(open(q)) == {"a": 1}
os.umask(old_umask); os.environ["OMANAGA_LAYOUTS"] = p
# the first run moves the Naga Control folder across once, and closes up what older versions left readable
home = tempfile.mkdtemp(); saved_env = os.environ["HOME"], os.environ.pop("OMANAGA_LAYOUTS"); os.environ["HOME"] = home
share = os.path.join(home, ".local/share"); old, new = os.path.join(share, "naga-control"), os.path.join(share, "omanaga")
assert c.layouts_path() == os.path.join(new, "layouts.json")
c.prepare_data_folder(); assert not os.path.exists(share), "nothing to move, so nothing is created"
os.makedirs(old, mode=0o755); layouts, state_file = os.path.join(old, "layouts.json"), os.path.join(old, "state.json")
for path in (layouts, state_file): open(path, "w").write('{"kept": 1}'); os.chmod(path, 0o644)
os.symlink("/nonexistent", os.path.join(old, "link"))
c.prepare_data_folder()
assert not os.path.exists(old) and json.load(open(c.layouts_path())) == {"kept": 1} and json.load(open(c.state_path())) == {"kept": 1}
assert os.stat(new).st_mode & 0o777 == 0o700 and all(os.stat(os.path.join(new, f)).st_mode & 0o777 == 0o600 for f in ("layouts.json", "state.json"))
os.makedirs(old); open(os.path.join(old, "layouts.json"), "w").write('{"stale": 1}'); os.chmod(os.path.join(old, "layouts.json"), 0o644)
c.prepare_data_folder(); assert json.load(open(c.layouts_path())) == {"kept": 1} and json.load(open(os.path.join(old, "layouts.json"))) == {"stale": 1}, "an existing folder wins"
assert os.stat(os.path.join(old, "layouts.json")).st_mode & 0o777 == 0o600, "a folder left behind is closed up too"
import shutil; shutil.rmtree(new); real_rename = os.rename
def no_rename(a, b): raise PermissionError(13, "no")
os.rename = no_rename; c.prepare_data_folder(); os.rename = real_rename; assert os.path.isdir(old) and not os.path.exists(new), "a failed move is tried again later"
real_talk = c.talk; c.talk = lambda argv: {"ok": True, "moved": os.path.isdir(new)}
assert c.main(["state"]) == {"ok": True, "moved": True}, "a real run does the move"; c.talk = real_talk
os.environ["HOME"], os.environ["OMANAGA_LAYOUTS"] = saved_env
os.makedirs(old := os.path.join(home, "other")); os.environ["OMANAGA_LAYOUTS"] = os.path.join(old, "layouts.json")
os.chmod(old, 0o755); c.prepare_data_folder(); assert os.stat(old).st_mode & 0o777 == 0o755, "a folder chosen by the variable is left alone"
os.environ["OMANAGA_LAYOUTS"] = p
# listing and deleting layouts need no mouse
c.store_layouts({"a": {}, "B": {}}); c.control_nodes = lambda: {}
r = c.main(["state"]); assert not r["ok"] and not r["connected"] and r["layouts"] == ["a", "B"]
r = c.main(["delete-layout", "a"]); assert r["ok"] and not r["connected"] and r["layouts"] == ["B"] and "error" not in r
r = c.main(["delete-layout", "nope"]); assert not r["ok"] and "No saved layout" in r["error"]
c.control_nodes = real_node
# single settings go back if the write doesn't stick, or if the read-back or the write itself fails
class Flaky(Fake):
    "the next write to a setting takes a wrong value (bad) or breaks the link (dead); dead_read breaks the verify read"
    mode = None; target = None
    def set_report(self, body):
        key = (body[6], body[7]); mode = self.mode
        if key == self.target and mode:
            self.mode = None
            if mode == "dead": raise OSError(5, "Input/output error")
            super().set_report(body)
            if mode == "bad": self.dpi, self.poll, self.idle = 9999, 4, 123
            if mode == "dead_after": self.fail_next = True
            return
        if getattr(self, "fail_next", False): self.fail_next = False; raise OSError(5, "Input/output error")
        super().set_report(body)
for setter, value, target, read in ((c.set_current_dpi, 2000, (4, 5), lambda f: f.dpi), (c.set_poll, 500, (0, 5), lambda f: f.poll), (c.set_idle, 600, (7, 3), lambda f: f.idle)):
    for mode in ("bad", "dead", "dead_after"):
        f = Flaky(); was = read(f); f.mode, f.target = mode, target
        try: setter(f, value); assert 0, (setter, mode)
        except (c.Err, OSError): pass
        assert read(f) == was, (setter, mode)
# transact waits through not-started and busy, resends on no reply, a timeout or status 3, and stops on a real refusal
class Replies(Fake):
    "plays these statuses for the next polls, then answers normally; None is a reply to some other command"
    def __init__(self, seq): super().__init__(); self.seq = list(seq)
    def get_report(self):
        r = bytearray(self.last)
        if self.seq:
            s = self.seq.pop(0)
            if s is None: r[7] ^= 0xff
            else: r[0] = s
        return bytes(r)
for seq, sends in (([0], 1), ([1, 1], 1), ([0, 1, 0], 1), ([None], 1), ([4], 2), ([4, 4], 3), ([1] * 20, 2), ([None] * 20, 2), ([1] * 40, 3),
                   ([3], 2), ([3, 3], 3), ([3, 4], 3), ([1] * 19 + [3], 2), ([3, 3, 3], 4), ([3, 3, 3, 3], 5), ([3, 4, 3], 4), ([4, 4, 3], 4)):
    f = Replies(seq); assert c.get_battery(f) == 80 and f.tx == sends, (seq, f.tx)
for seq, sends, text in (([4, 4, 4], 3, "didn't answer"), ([1] * 60, 3, "didn't answer"), ([None] * 60, 3, "didn't answer"), ([3] * 5, 5, "rejected"), ([3, 3, 4], 3, "didn't answer"), ([3, 4, 4], 3, "didn't answer"), ([3, 3, 3, 3, 4], 5, "didn't answer"), ([3, 3, 3, 4], 4, "didn't answer"), ([3, 3] + [1] * 20, 3, "didn't answer"), ([3, 3, 3] + [1] * 20, 4, "didn't answer"), ([5], 1, "support"), ([9], 1, "Unknown response 9")):
    f = Replies(seq)
    try: c.get_battery(f); assert 0, seq
    except c.Err as e: assert text in str(e) and f.tx == sends, (seq, f.tx, e)
# a link timeout on a write is resent, whether or not the first one landed, and only the button asked for changes
class Dropped(Fake):
    "these sends of a button write, counted from 1, get status 3; landed means they still took effect"
    def __init__(self, drops, landed=False): super().__init__(); self.drops, self.landed, self.sends = drops, landed, 0
    def set_report(self, body):
        if (body[6], body[7]) != (2, 0x0c): return super().set_report(body)
        self.sends += 1
        if self.sends not in self.drops: return super().set_report(body)
        if self.landed: super().set_report(body)
        r = bytearray(body); r[0] = 3; self.last = bytes(r)
for drops, landed in (({1}, False), ({1}, True), ({1, 2}, False), ({1, 2}, True), ({1, 2, 3, 4}, False), ({1, 2, 3, 4}, True)):
    f = Dropped(drops, landed); was = dict(f.mem); c.set_button(f, SIDE1, 0, ctrl1)
    assert f.mem[(1, 0, SIDE1)] == f.mem[(0, 0, SIDE1)] == c.encode(ctrl1, SIDE1), (drops, landed)
    assert {k: v for k, v in f.mem.items() if k[2] != SIDE1} == {k: v for k, v in was.items() if k[2] != SIDE1}, (drops, landed)
# every attempt refused still fails, with nothing changed, and the panel gets the error
f = Dropped(set(range(1, 100))); was = dict(f.mem)
try: c.set_button(f, SIDE1, 0, ctrl1); assert 0
except c.Err as e: assert str(e) == c.STATUS[3] and f.sends == c.LINK_ATTEMPTS and f.mem == was, e
# the rollback retries too: a link timeout during it puts the button back and reports only the first cause
f = Dropped(set(range(2, 8))); was = {k: v for k, v in f.mem.items() if k[0] == 1}
try: c.set_button(f, TOPF, 0, {"kind": "hypershift"}); assert 0
except c.Err as e: assert str(e) == c.STATUS[3], e
assert f.sends == 8 and {k: v for k, v in f.mem.items() if k[0] == 1} == was
# a rollback that still can't finish says what's left
f = Dropped(set(range(2, 100)))
try: c.set_button(f, TOPF, 0, {"kind": "hypershift"}); assert 0
except c.Err as e: assert c.STATUS[3] in str(e) and "may not be back" in str(e) and "Top button 1" in str(e), e
# a sleeping mouse answers everything with 4, and only moving it wakes it: fail fast, with the wake message, on the first read
class Asleep(Fake):
    def set_report(self, body): self.tx += 1; r = bytearray(body); r[0] = 4; self.last = bytes(r)
real_hid, real_node, stub_sleep, slept = c.Hid, c.control_nodes, time.sleep, []
c.control_nodes, time.sleep = (lambda: {"hyperspeed": "/dev/fake"}), slept.append
for argv in (["state"], ["battery"], ["set-poll", "500"], ["set-button", "64", "0", json.dumps(ctrl1)], ["preset", "factory"]):
    f = Asleep(); c.Hid = lambda path: f; slept.clear(); r = c.main(argv)
    assert {k: r[k] for k in ("ok", "connected", "permission", "error")} == {"ok": False, "connected": True, "permission": True, "error": "The mouse didn't answer. Move it to wake it, then try again."}, (argv, r)
    assert r["mice"] == [{"id": "hyperspeed", "name": "Naga V2 HyperSpeed"}], r
    assert f.tx == c.ATTEMPTS and sum(slept) < 1, (argv, f.tx, sum(slept))
c.Hid, c.control_nodes, time.sleep = real_hid, real_node, stub_sleep
# lowering the sleep timer can put the mouse to sleep at once: that's a success with a warning, and nothing goes back
class DropsOff(Fake):
    "accepts the sleep timer write, then answers everything with this status"
    def __init__(self, idle, status=4): super().__init__(); self.idle, self.status, self.off = idle, status, False
    def set_report(self, body):
        if self.off: self.tx += 1; r = bytearray(body); r[0] = self.status; self.last = bytes(r); return
        super().set_report(body)
        self.off = (body[6], body[7]) == (7, 0x03)
SLEPT = "Saved. The mouse went to sleep straight away because the new sleep time is shorter than it has been idle; move it to wake it."
def backend(f, argv):
    real_hid, real_node = c.Hid, c.control_nodes
    c.Hid, c.control_nodes = (lambda path: f), (lambda: {"hyperspeed": "/dev/fake"})
    try: return c.main(argv)
    finally: c.Hid, c.control_nodes = real_hid, real_node
f = DropsOff(900); r = backend(f, ["set-idle", "300"])
assert r == {"ok": True, "connected": True, "permission": True, "idle": 300, "warning": SLEPT} and f.idle == 300, r
f = DropsOff(900); f.mem[(1, 0, SIDE1)] = f.mem[(0, 0, SIDE1)] = c.encode(ctrl1, SIDE1); f.poll, f.dpi = 2, 999; c.set_loaded("x"); c.set_chosen({SIDE1})
r = backend(f, ["preset", "factory"])
assert r["ok"] and r["warning"] == SLEPT and r["idle"] == 300 and "buttons" not in r and "error" not in r, r
assert f.idle == 300 and f.poll == 1 and f.dpi == 1600 and f.stages == c.FACTORY_DPI and all(f.mem[(1, l, b)] == c.FACTORY[b] for b in c.CONTROLS for l in (0, 1))
assert c.get_loaded() == "" and c.get_chosen() == set(), "the reset's own notes still happen"
g = Fake(); g.mem[(1, 0, SIDE1)] = g.mem[(0, 0, SIDE1)] = c.encode(ctrl1, SIDE1); c.save_layout(g, "short")
f = DropsOff(900); r = backend(f, ["apply-layout", "short"])
assert r["ok"] and r["warning"] == SLEPT and f.idle == 300 and f.mem[(1, 0, SIDE1)] == c.encode(ctrl1, SIDE1) and c.get_loaded() == "short", r
# the timer's read-back works and the mouse falls asleep during the refresh after it: saved, and the panel still gets the new time
class DropsOffAfterVerify(Fake):
    "answers normally until the read-back after a sleep timer write, then answers everything with this status"
    def __init__(self, status=4): super().__init__(); self.idle, self.status, self.state = 900, status, 0
    def set_report(self, body):
        if self.state == 2: r = bytearray(body); r[0] = self.status; self.last = bytes(r); return
        super().set_report(body)
        if (body[6], body[7]) == (7, 0x03): self.state = 1
        elif self.state == 1 and (body[6], body[7]) == (7, 0x83): self.state = 2
OK = {"ok": True, "connected": True, "permission": True}
DONE = "Done, but the mouse stopped answering before this panel could refresh."
f = DropsOffAfterVerify(); r = backend(f, ["set-idle", "60"])
assert r == {**OK, "idle": 60, "warning": c.ASLEEP_AT_REFRESH} and f.idle == 60, r
f = DropsOffAfterVerify(); r = backend(f, ["apply-layout", "short"])
assert r == {**OK, "idle": 300, "warning": c.ASLEEP_AT_REFRESH} and f.idle == 300 and c.get_loaded() == "short", r
f = DropsOffAfterVerify(); r = backend(f, ["preset", "factory"])
assert r == {**OK, "idle": 300, "warning": c.ASLEEP_AT_REFRESH} and f.idle == 300, r
# any other refresh failure keeps the old wording, and the panel still gets the new time
for status, argv in ((3, ["set-idle", "60"]), (1, ["set-idle", "60"])):
    f = DropsOffAfterVerify(status); r = backend(f, argv)
    assert r == {**OK, "idle": 60, "warning": DONE} and f.idle == 60, (status, r)
# a command that didn't touch the timer has no time to send back
f = StopsAfter((0, 0x85), 2); r = backend(f, ["set-poll", "500"]); assert r == {**OK, "warning": c.ASLEEP_AT_REFRESH} and f.poll == 2, r
# deleting a layout saves nothing, so it doesn't say "Saved"
c.save_layout(Fake(), "gone"); r = backend(Asleep(), ["delete-layout", "gone"])
assert r == {**OK, "warning": DONE, "layouts": sorted(c.load_layouts(), key=str.lower)} and "gone" not in r["layouts"], r
# a save or delete that the mouse falls asleep after still sends the panel the layout list, from the file
f = StopsAfter((7, 0x83)); r = backend(f, ["save-layout", "Nap"])
assert r == {**OK, "warning": c.ASLEEP_AT_REFRESH, "layouts": sorted(c.load_layouts(), key=str.lower)} and "Nap" in r["layouts"], r
f = StopsAfter((7, 0x83), 0); r = backend(f, ["delete-layout", "Nap"])
assert r == {**OK, "warning": DONE, "layouts": sorted(c.load_layouts(), key=str.lower)} and "Nap" not in r["layouts"], r
# a command that didn't change the file has no list to send
f = StopsAfter((0, 0x85), 2); r = backend(f, ["set-poll", "500"]); assert "layouts" not in r, r
# the sleep timer is the last thing a layout or factory reset writes
class Order(Fake):
    def __init__(self): super().__init__(); self.idle, self.sets = 900, []
    def set_report(self, body):
        if body[7] < 0x80: self.sets.append((body[6], body[7]))
        super().set_report(body)
f = Order(); c.apply_preset(f, "factory"); assert f.sets[-1] == (7, 0x03) and f.sets.count((7, 0x03)) == 1, f.sets
f = Order(); c.apply_layout(f, "short"); assert f.sets[-1] == (7, 0x03) and f.sets.count((7, 0x03)) == 1, f.sets
# any other read-back failure still rolls back: it isn't asleep, or going longer can't have put it to sleep
for f, argv, text in ((DropsOff(900, 3), ["set-idle", "300"], "rejected"), (DropsOff(900, 1), ["set-idle", "300"], "didn't answer"), (DropsOff(300), ["set-idle", "600"], "didn't answer"),
                      (DropsOff(300), ["set-idle", "300"], "didn't answer"), (DropsOff(60), ["preset", "factory"], "didn't answer"), (DropsOff(900, 3), ["preset", "factory"], "rejected")):
    r = backend(f, argv)
    assert not r["ok"] and text in r["error"] and "may not be back" in r["error"] and "warning" not in r, (argv, r)
# a write that the mouse never took, because it was already asleep, isn't saved
f = StopsAfter((7, 0x83)); r = backend(f, ["set-idle", "60"]); assert not r["ok"] and "didn't answer" in r["error"] and f.idle == 300, r
# a value that reads back wrong goes back, even when lowering
class IdleWrong(Fake):
    def set_report(self, body):
        super().set_report(body)
        if (body[6], body[7]) == (7, 0x03) and self.idle == 60: self.idle = 123
f = IdleWrong(); f.idle = 900
try: c.set_idle(f, 60); assert 0
except c.Err as e: assert "Sleep timer didn't save" in str(e) and f.idle == 900, e
# a saved layouts file reaches the disk before it replaces the old one
real_fsync, real_replace, calls = os.fsync, os.replace, []
os.fsync = lambda fd: calls.append("sync"); os.replace = lambda a, b: (calls.append("replace"), real_replace(a, b))
c.store_layouts({"new": {}}); os.fsync, os.replace = real_fsync, real_replace
assert calls == ["sync", "replace"] and json.load(open(p)) == {"new": {}}
# the lock lives in the private runtime folder, never a shared one, and a sudo tool finds the user's copy
import contextlib, fcntl, io, stat
run_dir = tempfile.mkdtemp(); os.environ["XDG_RUNTIME_DIR"] = run_dir; os.environ.pop("SUDO_UID", None)
assert c.lock_path() == os.path.join(run_dir, f"omanaga-{os.getuid()}.lock")
real_isdir, real_geteuid, real_getuid = os.path.isdir, os.geteuid, os.getuid
os.environ["XDG_RUNTIME_DIR"] = os.path.join(run_dir, "missing"); os.path.isdir = lambda d: d == f"/run/user/{UID}"
assert c.lock_path() == f"/run/user/{UID}/omanaga-{UID}.lock", "falls back to /run/user"
os.path.isdir = lambda d: False
try: c.lock_path(); assert 0, "no shared fallback"
except c.Err as e: assert "XDG_RUNTIME_DIR" in str(e)
assert c.main(["state"])["error"].startswith("Can't find a private folder"), "the panel gets the reason"
os.path.isdir = real_isdir; os.environ["XDG_RUNTIME_DIR"] = run_dir
os.environ["SUDO_UID"] = "4242"; assert c.lock_owner() == UID or os.geteuid() == 0, "only root follows SUDO_UID"
os.geteuid = lambda: 0; os.getuid = lambda: 0; os.path.isdir = lambda d: d == "/run/user/4242"
assert c.lock_owner() == 4242 and c.lock_path() == "/run/user/4242/omanaga-4242.lock", "and it ignores the environment's folder"
os.path.isdir = real_isdir
real_lock_path = c.lock_path; root_lock = os.path.join(tempfile.mkdtemp(), "omanaga.lock"); c.lock_path = lambda: root_lock
os.environ["SUDO_UID"] = str(UID)
held = []
with contextlib.redirect_stderr(io.StringIO()) as warned, c.mouse_lock(): held.append(1)
assert held and "without one" in warned.getvalue() and not os.path.exists(root_lock), "root never creates the file"
open(root_lock, "w").close()
with c.mouse_lock():
    fd = os.open(root_lock, os.O_RDONLY)
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); assert 0, "root takes the user's lock"
    except BlockingIOError: pass
    os.close(fd)
os.environ["SUDO_UID"] = str(UID + 1)
try:
    with c.mouse_lock(): assert 0, "a file owned by someone else isn't used"
except c.Err as e: assert "isn't a file owned" in str(e)
os.geteuid, os.getuid = real_geteuid, real_getuid; del os.environ["SUDO_UID"]
target = os.path.join(tempfile.mkdtemp(), "target"); open(target, "w").close(); os.remove(root_lock); os.symlink(target, root_lock)
try:
    with c.mouse_lock(): assert 0, "a link isn't followed"
except c.Err as e: assert "Couldn't open" in str(e)
os.remove(root_lock); os.mkfifo(root_lock)
try:
    with c.mouse_lock(): assert 0, "a FIFO neither blocks nor counts"
except c.Err as e: assert "isn't a file" in str(e)
os.remove(root_lock); os.symlink(os.path.join(os.path.dirname(target), "absent"), root_lock)
try:
    with c.mouse_lock(): assert 0, "a dangling link isn't created through"
except c.Err: assert not os.path.exists(os.path.join(os.path.dirname(target), "absent"))
c.lock_path = real_lock_path
with c.mouse_lock():
    assert stat.S_IMODE(os.stat(c.lock_path()).st_mode) == 0o600, "a new lock is owner-only"
    fd = os.open(c.lock_path(), os.O_RDONLY)
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); assert 0, "held"
    except BlockingIOError: pass
    os.close(fd)
    real_wait = c.LOCK_WAIT; c.LOCK_WAIT = 0
    try:
        with c.mouse_lock(): assert 0, "the wait for a held lock ends"
    except c.Err as e: assert "still using" in str(e)
    c.LOCK_WAIT = real_wait
with c.mouse_lock(): pass  # released, and an existing file opens fine
os.chmod(c.lock_path(), 0o444)
with c.mouse_lock(): pass  # a lock file this user can't write still works
c.control_nodes = lambda: {"hyperspeed": "/dev/hidraw-test"}; c.Hid = lambda path: Fake()
assert c.main(["state"])["ok"]; c.Hid, c.control_nodes = real_hid, real_node
# a failure at the last step of an all-or-nothing change also puts back the polling rate before it
class IdleStuck(Fake):
    def set_report(self, body):
        keep = self.idle; super().set_report(body)
        if (body[6], body[7]) == (7, 0x03) and int.from_bytes(body[8:10], "big") == 300: self.idle = keep
f = IdleStuck(); f.poll, f.idle = 2, 600; was = dict(f.mem)
try: c.apply_preset(f, "factory"); assert 0
except c.Err as e: assert "Sleep" in str(e)
assert f.mem == was and f.poll == 2 and f.idle == 600
# DPI and polling go back before the buttons, since undo gives up after 3 failures, and the sleep timer last, since lowering it can put the mouse to sleep
real_undo, labels = c.undo, []
def spy(original, steps): labels.extend(label for label, _ in steps); real_undo(original, steps)
c.undo = spy; f = IdleStuck(); f.poll, f.idle = 2, 600
try: c.apply_preset(f, "factory"); assert 0
except c.Err: pass
c.undo = real_undo; assert labels[:2] == ["DPI", "polling rate"] and labels[-1] == "sleep timer" and labels[2:-1] and set(labels[2:-1]) <= set(c.CONTROLS.values()), labels
# the same button's two layers are never read back to back, on any path, because only the button id can expose a stale reply
class Track(Fake):
    def __init__(self, *a, **k): super().__init__(*a, **k); self.sent = []
    def set_report(self, body):
        self.sent.append(tuple(body[8:11]) if (body[6], body[7]) == (2, 0x8c) else None); super().set_report(body)
    def check(self):
        for x, y in zip(self.sent, self.sent[1:]):
            assert not (x and y and x[:2] == y[:2] and x[2] != y[2]), (x, y)
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
LAST = list(c.CONTROLS)[-1]
assert [k for k in c.read_buttons(Fake())] == [(b, 0) for b in c.CONTROLS] + [(b, 1) for b in c.CONTROLS]
for argv in (["state"], ["save-layout", "x"], ["apply-layout", "x"], ["preset", "factory"], ["preset", "wow"],
             ["set-button", str(LAST), "0", json.dumps(ctrl1)], ["set-button", str(TOPF), "0", json.dumps({"kind": "hypershift"})]):
    f = Track(); c.run(f, argv); f.check(); assert f.mem[(1, 0, 1)] == bytes([1, 1, 1, 0, 0, 0, 0])
f = Track(); c.snapshot(f); f.check()
f = Track(); c.put_many(f, [(SIDE1, 0, c.encode(ctrl1, SIDE1)), (SIDE1, 1, c.encode(ctrl1, SIDE1))]); f.check()
for kw in ({"reject": 1}, {"reject": 2}, {"corrupt": 1}):  # rollback too
    f = Track(**kw)
    try: c.set_button(f, LAST, 0, ctrl1); assert 0, kw
    except c.Err: pass
    f.check()
# one poll of lag gets every record right under this ordering, and an edit to the last button read works
f = Lag(); f.mem[(1, 1, SIDE1)] = c.encode({"kind": "media", "value": "mute"}, SIDE1); f.mem[(1, 1, LAST)] = c.DISABLED
want = {k: f.mem[(1, k[1], k[0])] for k in c.read_buttons(Fake())}
assert c.read_buttons(f) == want
f = Lag(); f.mem[(1, 1, LAST)] = c.encode(ctrl1, LAST); f.mem[(0, 1, LAST)] = c.encode(ctrl1, LAST)  # layer 1 already holds the new value
c.set_button(f, LAST, 0, ctrl1); assert f.mem[(1, 0, LAST)] == f.mem[(0, 0, LAST)] == c.encode(ctrl1, LAST)
# probe.py stops with a clear message, not a traceback, if what it's piped into goes away
import subprocess
tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
script = f"""import sys; sys.path.insert(0, {tools!r}); import probe
probe.os.geteuid = lambda: 0; probe.snapshot = lambda: None
def watch(seconds):
    while True: print("key", flush=True)
probe.watch = watch; probe.main()"""
p = subprocess.Popen([sys.executable, "-B", "-I", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
p.stdout.close(); err = p.stderr.read(); p.stderr.close(); assert p.wait(30) == 1 and "Stopped" in err and "Traceback" not in err, err
# the third byte of a button reply says nothing about the layer: replies from the real mouse, both layers of side 1 and side 7
class Captured(Fake):
    def set_report(self, body):
        super().set_report(body)
        if (body[6], body[7]) == (2, 0x8c):
            r = bytearray(self.last); r[10:18] = {(0x40, 0): bytes.fromhex("0102 0201 1e00 0000".replace(" ", "")), (0x40, 1): bytes.fromhex("0102 0100 1e00 0000".replace(" ", ""))}.get((body[9], body[10]), bytes(r[10:18])); self.last = bytes(r)
f = Captured(); assert c.read_rec(f, 1, 0x40, 0) == bytes.fromhex("0202011e000000") and c.read_rec(f, 1, 0x40, 1) == c.FACTORY[0x40] and c.read_rec(f, 1, 0x46, 1) == c.FACTORY[0x46]
# DPI stages match the factory format from the real mouse: the DPI in all three slots, whatever saved them
FACTORY_STAGES = "01 03 05 01 01 90 01 90 01 90 02 03 20 03 20 03 20 03 06 40 06 40 06 40 04 0c 80 0c 80 0c 80 05 19 00 19 00 19 00"
def slots_match(f): return all(s[1:3] == s[3:5] == s[5:7] for s in f.stage_recs) and [int.from_bytes(s[5:7], "big") for s in f.stage_recs] == f.stages
f = Fake(); assert c.transact(f, 4, 0x86, 0x26, b"\x01").hex(" ") == FACTORY_STAGES
f = Fake(); f.stage_recs = [s[:5] + b"\0\0" for s in f.stage_recs]; assert c.get_stages(f) == (c.FACTORY_DPI, 3), "reads the first value, even where a save by an older version zeroed the third"
c.set_dpi(f, c.FACTORY_DPI, 3); assert c.transact(f, 4, 0x86, 0x26, b"\x01").hex(" ") == FACTORY_STAGES
f = Fake(); c.set_dpi(f, [500, 1000, 2000, 4000, 8000], 2); assert slots_match(f) and f.stages == [500, 1000, 2000, 4000, 8000]
f.stage_recs = [s[:5] + b"\0\0" for s in f.stage_recs]; c.apply_preset(f, "factory"); assert slots_match(f) and f.stages == c.FACTORY_DPI
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
g = Fake(); c.set_dpi(g, [600, 1200, 2400, 4800, 9600], 1); c.save_layout(g, "dpi")
f = Fake(); f.stage_recs = [s[:5] + b"\0\0" for s in f.stage_recs]; c.apply_layout(f, "dpi"); assert slots_match(f) and f.stages == [600, 1200, 2400, 4800, 9600]
f = Fake(reject="stages"); c_before = list(f.stage_recs)
try: c.set_dpi(f, [500] * 5, 1); assert 0
except c.Err: assert f.stage_recs == c_before
# only the layout last loaded is marked, and only while the mouse still matches it
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
f = Fake(); assert c.run(f, ["state"])["loadedLayout"] == ""
c.run(f, ["save-layout", "Backup"]); st = c.run(f, ["save-layout", "new"]); assert st["loadedLayout"] == "", "saving doesn't mark"
st = c.run(f, ["apply-layout", "new"]); assert st["loadedLayout"] == "new", "of two identical layouts, the one loaded"
st = c.run(f, ["save-layout", "another"]); assert st["loadedLayout"] == "new", "saving doesn't move the mark"
st = c.run(f, ["save-layout", "new"]); assert st["loadedLayout"] == "new", "replacing the loaded layout keeps it"
st = c.run(f, ["set-poll", "500"]); assert st["loadedLayout"] == "", "a changed setting unmarks"
st = c.run(f, ["set-poll", "1000"]); assert st["loadedLayout"] == "new", "and the mouse matching again marks it again"
st = c.run(f, ["set-button", str(SIDE1), "0", json.dumps(ctrl1)]); assert st["loadedLayout"] == "", "so does a changed button"
c.run(f, ["apply-layout", "Backup"]); st = c.run(f, ["delete-layout", "new"]); assert st["loadedLayout"] == "Backup", "deleting another layout leaves it"
st = c.run(f, ["delete-layout", "Backup"]); assert st["loadedLayout"] == "" and json.load(open(c.state_path()))["loaded"] == "", "deleting it clears it"
st = c.run(f, ["save-layout", "Backup"]); assert st["loadedLayout"] == "", "a new layout with the old name isn't marked"
# both presets clear it, even if the mouse would match again afterwards
c.run(f, ["apply-layout", "Backup"]); st = c.run(f, ["preset", "factory"]); assert st["loadedLayout"] == "" and json.load(open(c.state_path()))["loaded"] == ""
st = c.run(f, ["save-layout", "Backup"]); c.run(f, ["apply-layout", "Backup"]); st = c.run(f, ["preset", "wow"]); assert st["loadedLayout"] == "" and json.load(open(c.state_path()))["loaded"] == ""
# a failed load doesn't mark, and leaves the existing mark alone
c.run(f, ["save-layout", "wow"]); c.run(f, ["apply-layout", "wow"]); c.run(f, ["preset", "factory"]); c.run(f, ["save-layout", "plain"]); c.run(f, ["apply-layout", "plain"])
f.reject = "stages"
try: c.run(f, ["apply-layout", "wow"]); assert 0
except c.Err: pass
assert c.run(f, ["state"])["loadedLayout"] == "plain"
# layouts files from before this existed work as they are, and the plain {name: layout} format doesn't change
os.remove(c.state_path()); data = c.load_layouts(); assert "loaded" not in data and "plain" in data
st = c.run(f, ["state"]); assert st["loadedLayout"] == "" and "plain" in st["layouts"]
st = c.run(f, ["apply-layout", "plain"]); assert st["loadedLayout"] == "plain" and set(json.load(open(c.layouts_path()))) == set(data)
# a damaged marker file only loses the mark
for junk in ("{oops", "[]", '{"loaded": 5}', '{"loaded": "gone"}'):
    open(c.state_path(), "w").write(junk); st = c.run(f, ["state"]); assert st["ok"] and st["loadedLayout"] == "" and "layoutsError" not in st, junk
# not being able to write the marker doesn't fail a load that worked
real_write = c.write_json
def no_write(path, data): raise OSError(30, "Read-only file system")
c.write_json = no_write; st = c.run(f, ["apply-layout", "plain"]); c.write_json = real_write; assert st["ok"]
# a Hypershift entry set on purpose survives moving the key, even when it looks unassigned
SIDE9, TOP2 = 0x48, 0x0c
def move_key(f):
    c.set_button(f, TOPF, 0, {"kind": "key", "key": "z"}); c.set_button(f, TOP2, 0, {"kind": "hypershift"})
def keyed():
    f = Fake(); c.set_button(f, SIDE1, 0, ctrl1); c.set_button(f, SIDE9, 0, {"kind": "key", "key": "x"}); c.set_button(f, TOPF, 0, {"kind": "hypershift"})
    return f
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
f = keyed(); c.set_button(f, SIDE9, 1, {"kind": "disabled"}); move_key(f)
assert f.mem[(1, 1, SIDE9)] == c.DISABLED and f.mem[(1, 0, SIDE9)] == c.key_rec(c.KEYS["x"])
assert f.mem[(1, 0, TOP2)] == f.mem[(1, 1, TOP2)] == c.HYPERSHIFT and f.mem[(1, 0, TOPF)] == f.mem[(1, 1, TOPF)] == c.key_rec(c.KEYS["z"])
assert f.mem[(1, 1, SIDE1)] == c.encode(ctrl1, SIDE1), "entries not chosen still mirror"
# the same with the factory action, set on purpose while the normal layer differs
c.apply_preset(f, "factory"); f = keyed(); c.set_button(f, SIDE9, 1, {"kind": "factory"}); move_key(f)
assert f.mem[(1, 1, SIDE9)] == c.FACTORY[SIDE9] != f.mem[(1, 0, SIDE9)]
# factory reset forgets them, and with no record the entry is treated as unassigned, as before
c.apply_preset(f, "factory"); assert c.get_chosen() == set() and json.load(open(c.state_path()))["chosen"] == []
f = keyed(); c.set_button(f, SIDE9, 1, {"kind": "disabled"}); os.remove(c.state_path()); move_key(f)
assert f.mem[(1, 1, SIDE9)] == c.key_rec(c.KEYS["x"]), "no state.json"
f = keyed(); f.mem[(1, 1, SIDE9)] = c.DISABLED; move_key(f)
assert f.mem[(1, 1, SIDE9)] == c.key_rec(c.KEYS["x"]), "blank and never chosen still copies"
# a failed write records nothing, and a normal-layer change that wrote the entry forgets it
c.apply_preset(f, "factory"); f = keyed(); f.reject = len(f.writes) + 1
try: c.set_button(f, SIDE9, 1, {"kind": "disabled"}); assert 0
except c.Err: assert c.get_chosen() == set()
f.reject = None; c.set_button(f, SIDE9, 1, {"kind": "key", "key": "x"}); assert c.get_chosen() == {SIDE9}
old_key(f, SIDE9); c.set_button(f, SIDE9, 0, {"kind": "disabled"}); assert c.get_chosen() == set() and f.mem[(1, 1, SIDE9)] == c.DISABLED
# the Ctrl+1-6 preset leaves chosen entries alone, even one that matches the normal action, and forgets only what it wrote
c.apply_preset(f, "factory"); f = keyed(); c.set_button(f, 0x41, 0, {"kind": "key", "key": "q"}); c.set_button(f, 0x41, 1, {"kind": "key", "key": "q"}); c.set_button(f, SIDE9, 1, {"kind": "disabled"})
c.apply_preset(f, "wow"); assert c.get_chosen() == {0x41, SIDE9} and c.get_loaded() == "" and f.mem[(1, 1, 0x41)] == c.key_rec(c.KEYS["q"]) != f.mem[(1, 0, 0x41)]
f = keyed(); c.set_chosen({SIDE1}); old_key(f, SIDE1); c.apply_preset(f, "wow"); assert c.get_chosen() == set() and f.mem[(1, 1, SIDE1)] == f.mem[(1, 0, SIDE1)]
# a layout keeps its record: loading it restores the record and the mark still works, and one saved before this existed has none
c.apply_preset(f, "factory"); f = keyed(); c.set_button(f, SIDE9, 1, {"kind": "disabled"}); st = c.run(f, ["save-layout", "kept"]); assert c.load_layouts()["kept"]["chosen"] == [SIDE9]
c.apply_preset(f, "factory"); assert c.get_chosen() == set()
st = c.run(f, ["apply-layout", "kept"]); assert st["loadedLayout"] == "kept" and c.get_chosen() == {SIDE9} and c.get_loaded() == "kept"
move_key(f); assert f.mem[(1, 1, SIDE9)] == c.DISABLED
c.apply_preset(f, "factory"); f = keyed(); c.set_button(f, SIDE9, 1, {"kind": "disabled"}); c.run(f, ["save-layout", "old"])
data = c.load_layouts(); del data["old"]["chosen"]; c.store_layouts(data); c.run(f, ["apply-layout", "kept"]); st = c.run(f, ["apply-layout", "old"])
assert st["loadedLayout"] == "old" and c.get_chosen() == set(); move_key(f); assert f.mem[(1, 1, SIDE9)] == c.key_rec(c.KEYS["x"])
# a damaged record means no record, and one in a layout means the layout is damaged
for junk in ('{"chosen": 5}', '{"chosen": [[]]}', '[]', '{oops'):
    open(c.state_path(), "w").write(junk); assert c.get_chosen() == set(), junk
for junk in (5, [[]], [1], ["0x40"]):
    data = c.load_layouts(); data["kept"]["chosen"] = junk; c.store_layouts(data)
    try: c.run(f, ["apply-layout", "kept"]); assert 0, junk
    except c.Err as e: assert "damaged" in str(e), junk
# a chosen entry that matches the normal action isn't a mirror, whichever layer was set first (Side 10, Disabled, then F15)
SIDE10, F15 = 0x49, c.key_rec(c.KEYS["F15"])
for first, second in ((1, 0), (0, 1)):
    c.apply_preset(f, "factory"); f = keyed()
    c.set_button(f, SIDE10, first, {"kind": "disabled"}); c.set_button(f, SIDE10, second, {"kind": "disabled"})
    assert f.mem[(1, 0, SIDE10)] == f.mem[(1, 1, SIDE10)] == c.DISABLED and c.get_chosen() == {SIDE10}, first
    c.set_button(f, SIDE10, 0, {"kind": "key", "key": "F15"})
    assert f.mem[(1, 0, SIDE10)] == f.mem[(0, 0, SIDE10)] == F15 and f.mem[(1, 1, SIDE10)] == f.mem[(0, 1, SIDE10)] == c.DISABLED, first
    assert c.get_chosen() == {SIDE10} and json.load(open(c.state_path()))["chosen"] == [SIDE10], first
    # and it stays apart for later changes, while one never chosen still mirrors
    c.set_button(f, SIDE10, 0, {"kind": "key", "key": "F16"}); assert f.mem[(1, 1, SIDE10)] == c.DISABLED, first
    c.set_button(f, 0x4a, 0, {"kind": "disabled"}); assert f.mem[(1, 1, 0x4a)] == c.DISABLED
    c.set_button(f, 0x4a, 0, {"kind": "key", "key": "F15"}); assert f.mem[(1, 1, 0x4a)] == F15 and c.get_chosen() == {SIDE10}
# the battery alert fires once per drop: 15% or under to fire, over 20% to re-arm, and state.json carries it across instances and restarts
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
def raw(pct): return round(pct * 255 / 100)
f = Fake()
def alert(pct):
    f.battery = raw(pct); r = c.run(f, ["battery"]); assert r["battery"] == pct, pct
    return r["alert"]
assert [alert(p) for p in (80, 21, 16)] == [False] * 3 and not os.path.exists(c.state_path()), "no writes until there's something to record"
assert alert(15) is True and json.load(open(c.state_path()))["batteryAlerted"] is True
assert [alert(p) for p in (15, 14, 3, 18, 20, 12)] == [False] * 6, "a bounce under the re-arm line stays quiet"
assert alert(21) is False and json.load(open(c.state_path()))["batteryAlerted"] is False
assert alert(40) is False and alert(13) is True and alert(13) is False
# a low battery that's already been seen stays quiet from a fresh start, and other state.json notes survive
c.set_loaded("kept"); assert alert(10) is False and c.get_loaded() == "kept"
assert alert(90) is False and c.get_loaded() == "kept"
# only the battery command alerts, and it doesn't use up the alert
f.battery = raw(10); assert "alert" not in c.run(f, ["state"]) and alert(10) is True
# a damaged state file only means the alert may fire again
for junk in ("{oops", "[]", '{"batteryAlerted": 5}'):
    open(c.state_path(), "w").write(junk); assert alert(10) is True, junk
    assert alert(10) is False, junk
# any number of backends running at once alert once, because the check is under the mouse lock
import select, threading
real_update = c.update_state
def slow_update(**values):
    select.select([], [], [], 0.02)  # time.sleep is stubbed out; this leaves room for a race if the lock isn't there
    real_update(**values)
os.remove(c.state_path()); f.battery = raw(10); answers = []
real_hid, real_node = c.Hid, c.control_nodes
c.Hid, c.control_nodes, c.update_state = (lambda path: f), (lambda: {"hyperspeed": "/dev/fake"}), slow_update
threads = [threading.Thread(target=lambda: answers.append(c.main(["battery"]))) for _ in range(6)]
for t in threads: t.start()
for t in threads: t.join()
c.Hid, c.control_nodes, c.update_state = real_hid, real_node, real_update
assert len(answers) == 6 and all(a["ok"] and a["battery"] == 10 for a in answers), answers
assert sum(a["alert"] for a in answers) == 1, answers
# only the top buttons can be the Hypershift key, and only the side buttons have a layer to edit: refused plainly, with nothing written
os.environ["OMANAGA_LAYOUTS"] = os.path.join(tempfile.mkdtemp(), "layouts.json")
MIDDLE, TILTL, TILTR = 0x03, 0x34, 0x35
hs = {"kind": "hypershift"}
assert c.KEY_BUTTONS == {TOPF, TOP2} and c.SIDE_BUTTONS == set(range(0x40, 0x4c))
f = Fake()
for b in c.CONTROLS:
    if b in c.KEY_BUTTONS: continue
    tx = f.tx
    try: c.set_button(f, b, 0, hs); assert 0, b
    except c.Err as e: assert str(e) == "Only Top button 1 and Top button 2 can be the Hypershift key.", b
    assert not f.writes and f.tx == tx, "refused without talking to the mouse"
for b in (TOPF, TOP2): c.set_button(Fake(), b, 0, hs)
f = keyed(); n = len(f.writes)
for b in (TOPF, TOP2, MIDDLE, TILTL, TILTR):
    try: c.set_button(f, b, 1, ctrl1); assert 0, b
    except c.Err as e: assert str(e) == "Only the side buttons can have a Hypershift action.", b
assert len(f.writes) == n
for b in sorted(c.SIDE_BUTTONS): c.set_button(f, b, 1, {"kind": "key", "key": "z"}); assert f.mem[(1, 1, b)] == c.key_rec(c.KEYS["z"]), b
# the state tells the panel which is which
st = c.get_state(f)
assert {b["id"] for b in st["buttons"] if b["canBeKey"]} == {TOPF, TOP2} and {b["id"] for b in st["buttons"] if b["layerEditable"]} == c.SIDE_BUTTONS
# a key on another button, from an old layout, still shows, and can be changed away on both layers
for b in (MIDDLE, SIDE1):
    c.apply_preset(f, "factory"); f = Fake(); old_key(f, b); st = c.get_state(f)
    assert st["hasHypershift"] and next(x for x in st["buttons"] if x["id"] == b)["normal"]["kind"] == "hypershift", b
    try: c.set_button(f, b, 0, hs); assert 0, b
    except c.Err as e: assert "Top button" in str(e), b
    c.set_button(f, b, 0, {"kind": "disabled"}); assert f.mem[(1, 0, b)] == f.mem[(1, 1, b)] == c.DISABLED and not c.get_state(f)["hasHypershift"], b
# other edits still work beside one
f = Fake(); old_key(f, MIDDLE); c.set_button(f, SIDE1, 1, ctrl1); assert f.mem[(1, 1, SIDE1)] == c.encode(ctrl1, SIDE1) and f.mem[(1, 0, MIDDLE)] == c.HYPERSHIFT
# a saved layout restores exactly what it holds, old keys and layer entries on other buttons included
f = Fake(); old_key(f, MIDDLE); f.mem[(1, 1, TILTL)] = c.encode(ctrl1, TILTL); c.run(f, ["save-layout", "old key"]); saved = c.snapshot(f)
c.apply_preset(f, "factory"); assert f.mem[(1, 0, MIDDLE)] != c.HYPERSHIFT
st = c.run(f, ["apply-layout", "old key"]); assert st["ok"] and st["loadedLayout"] == "old key" and c.snapshot(f) == saved
assert f.mem[(1, 0, MIDDLE)] == f.mem[(1, 1, MIDDLE)] == c.HYPERSHIFT and f.mem[(1, 1, TILTL)] == c.encode(ctrl1, TILTL)
# a leftover entry on a button with no editable layer follows the next normal-layer change, since nothing else can reach it
c.set_button(f, TILTL, 0, {"kind": "key", "key": "z"}); assert f.mem[(1, 0, TILTL)] == f.mem[(1, 1, TILTL)] == c.key_rec(c.KEYS["z"])
c.set_chosen({TILTR}); c.set_button(f, TILTR, 0, {"kind": "key", "key": "x"}); assert f.mem[(1, 1, TILTR)] == c.key_rec(c.KEYS["x"]) and c.get_chosen() == set()
# the level to highlight: only one whose value is the DPI, otherwise none
stages = [400, 800, 1600, 3200, 6400]
assert [c.dpi_level(stages, 3, d) for d in (1600, 800, 6400, 1234)] == [3, 2, 5, 0]
assert c.dpi_level([800, 800, 1600, 800, 3200], 4, 800) == 4 and c.dpi_level([800, 800, 1600, 800, 3200], 3, 800) == 1
f = Fake(); assert c.run(f, ["state"])["dpiLevel"] == 3
st = c.run(f, ["set-current-dpi", "1234"]); assert st["dpi"] == 1234 and st["activeStage"] == 3 and st["dpiLevel"] == 0
st = c.run(f, ["set-current-dpi", "800"]); assert st["dpiLevel"] == 2 and st["stages"] == stages and f.active == 3
st = c.run(f, ["set-dpi", ",".join(map(str, stages)), "4"]); assert st["dpi"] == 3200 and st["dpiLevel"] == 4
# saving the levels with none chosen keeps a custom DPI and the active level
f = Fake(); c.run(f, ["set-current-dpi", "1234"]); st = c.run(f, ["set-dpi", "500,1000,2000,4000,8000", "keep"])
assert f.stages == [500, 1000, 2000, 4000, 8000] and f.active == 3 and f.dpi == 1234 and st["dpiLevel"] == 0
st = c.run(f, ["set-dpi", "500,1234,2000,4000,8000", "keep"]); assert f.dpi == 1234 and st["dpiLevel"] == 2 and f.active == 3
# and puts everything back if the mouse doesn't take them
f = Fake(reject="stages"); f.dpi = 1234
try: c.set_dpi(f, [500, 1000, 2000, 4000, 8000], None); assert 0
except c.Err: assert f.stages == c.FACTORY_DPI and f.active == 3 and f.dpi == 1234
for bad in (0, 6):
    try: c.set_dpi(Fake(), c.FACTORY_DPI, bad); assert 0, bad
    except c.Err: pass
# a button action arrives on stdin, bounded in time and size, and a stuck run stops itself
import subprocess
real_hid, real_node = c.Hid, c.control_nodes; f = Fake(); c.Hid, c.control_nodes = (lambda path: f), (lambda: {"hyperspeed": "/dev/fake"})
def over_stdin(text, argv=("set-button", str(SIDE1), "0")):
    r, w = os.pipe(); saved = os.dup(0); os.dup2(r, 0); os.close(r)
    if text is not None: os.write(w, text.encode())
    try: return c.main(list(argv))
    finally: os.dup2(saved, 0); os.close(saved); os.close(w)
assert over_stdin(json.dumps(ctrl1) + "\n")["ok"] and c.read_rec(f, c.STORED, SIDE1) == c.key_rec(0x1e, c.MODS["ctrl"]), "it's applied"
real_wait = c.INPUT_WAIT; c.INPUT_WAIT = 0
assert "didn't send" in over_stdin(None)["error"], "a panel that never writes doesn't hang it"; c.INPUT_WAIT = real_wait
assert "Bad request" in over_stdin("{bad")["error"] and "Bad request" in over_stdin("\xff")["error"]
assert "Bad request" in over_stdin("x" * 9000)["error"], "only a bounded amount is read"
c.Hid, c.control_nodes = real_hid, real_node
# the Naga V2 Pro: its own buttons, all three side plates, two copies per DPI level, and its own layouts and notes
c.write_json(c.state_path(), {}); c.use_model("pro"); f = Fake(); st = c.get_state(f)
ids = [b["id"] for b in st["buttons"]]
assert st["mouse"] == "pro" and st["mouseName"] == "Naga V2 Pro" and len(ids) == 26 and 0x0b not in ids and 0x0c not in ids
assert {b["plate"] for b in st["buttons"] if b["id"] in range(0x50, 0x56)} == {6} and [p["buttons"] for p in st["plates"]] == [12, 6, 2]
assert c.describe(c.FACTORY[0x04])["label"] == "Back" and c.describe(c.FACTORY[0x55])["label"] == "6" and not f.writes
c.set_button(f, 0x50, 0, ctrl1); assert f.mem[(1, 0, 0x50)] == f.mem[(0, 0, 0x50)] == c.encode(ctrl1, 0x50)
try: c.set_button(f, 0x0b, 0, ctrl1); assert 0, "the Pro has no top buttons"
except c.Err: pass
try: c.set_button(f, 0x34, 0, {"kind": "hypershift"}); assert 0, "tilt can't hold Hypershift"
except c.Err: pass
assert {b["id"] for b in st["buttons"] if b["canBeKey"]} == set(range(0x40, 0x4c)) | set(range(0x50, 0x56)) | {0x04, 0x05, 0x03}
assert c.FACTORY[0x6d] == c.FACTORY[0x60] == c.FACTORY[0x0e] == c.DISABLED, "factory leaves the DPI and profile buttons off"
c.set_button(f, 0x4b, 0, {"kind": "hypershift"}); assert f.mem[(1, 1, 0x4b)] == c.HYPERSHIFT
c.set_button(f, 0x4b, 0, c.describe(c.FACTORY[0x4b]) | {"kind": "factory"}); assert f.mem[(1, 0, 0x4b)] == c.FACTORY[0x4b]
c.set_button(f, 0x04, 0, {"kind": "hypershift"}); c.set_button(f, 0x51, 1, {"kind": "media", "value": "mute"})
assert f.mem[(1, 1, 0x51)] == bytes([0x0a, 2, 0, 0xe2, 0, 0, 0]) and f.mem[(1, 0, 0x51)] == c.FACTORY[0x51]
c.set_dpi(f, [500, 1000, 2000, 4000, 8000], 2)
assert all(r[1:3] == r[3:5] and r[5:7] == b"\0\0" for r in f.stage_recs) and f.dpi == 1000, "two copies, then zeros"
c.apply_preset(f, "wow"); assert all(f.mem[(1, 0, b + i)] == c.key_rec(0x1e + i, 1) for b in (0x40, 0x50) for i in range(6))
c.store_layouts({}); c.save_layout(f, "pro one"); saved = c.load_layouts()["pro one"]
assert saved["model"] == "pro" and "0x50" in saved["buttons"] and c.layout_names() == ["pro one"]
assert "pro.chosen" in c.read_state() and c.get_chosen() == {0x51}
c.apply_preset(f, "factory"); c.apply_layout(f, "pro one"); assert c.get_state(f)["loadedLayout"] == "pro one"
c.use_model("hyperspeed"); h = Fake()
assert c.layout_names() == [] and c.get_loaded() == "" and c.get_chosen() == set(), "the HyperSpeed doesn't see the Pro's layouts or notes"
for bad in (lambda: c.apply_layout(h, "pro one"), lambda: c.delete_layout("pro one"), lambda: c.save_layout(h, "pro one")):
    try: bad(); assert 0
    except c.Err: pass
assert not h.writes and "pro one" in c.load_layouts()
c.save_layout(h, "hs"); assert "model" not in c.load_layouts()["hs"], "HyperSpeed layouts keep their old shape"
c.use_model("pro"); assert c.layout_names() == ["pro one"]; c.use_model("hyperspeed")
# the dock is found as the Pro, both mice can be plugged in, and the panel picks between them
c.glob = FakeGlob; hidraw("hidraw7", "8-1:1.0", "0003:00001532:000000A4"); hidraw("hidraw8", "8-1:1.1", "0003:00001532:000000A4")
assert c.control_nodes() == {"hyperspeed": "/dev/hidraw4", "pro": "/dev/hidraw7"}; c.glob = real_glob
mice = {"/dev/hs": Fake()}; c.use_model("pro"); mice["/dev/pro"] = Fake(); c.use_model("hyperspeed")
real_hid, real_node = c.Hid, c.control_nodes
c.Hid, c.control_nodes = (lambda path: mice[path]), (lambda: {"hyperspeed": "/dev/hs", "pro": "/dev/pro"})
c.update_state(mouse=None)
r = c.main(["state"]); assert r["mouse"] == "hyperspeed" and [m["id"] for m in r["mice"]] == ["hyperspeed", "pro"], "first found when none picked"
r = c.main(["select-mouse", "pro"]); assert r["ok"] and r["mouse"] == "pro" and len(r["buttons"]) == 26
assert c.main(["state"])["mouse"] == "pro" and c.main(["battery"])["mouse"] == "pro", "the pick sticks"
assert not c.main(["select-mouse", "mx"])["ok"]
class Sleeping(Fake):
    def get_report(self): return bytes([4]) + self.last[1:]
c.use_model("hyperspeed"); mice["/dev/hs"] = Sleeping(); c.update_state(mouse="hyperspeed")
r = c.main(["state"]); assert not r["ok"] and [m["id"] for m in r["mice"]] == ["hyperspeed", "pro"], "a sleeping mouse still lists both, so the panel can switch"
assert c.main(["select-mouse", "pro"])["ok"]
c.control_nodes = lambda: {"hyperspeed": "/dev/hs"}; assert c.main(["state"])["mouse"] == "hyperspeed", "falls back when the pick is unplugged"
c.control_nodes = lambda: {}; r = c.main(["state"]); assert r["mouse"] == "pro" and r["layouts"] == ["pro one"], "unplugged lists the last mouse's layouts"
c.Hid, c.control_nodes = real_hid, real_node; c.use_model("hyperspeed"); c.update_state(mouse=None)
# run as a real program, with the mouse search removed so this can never reach a real mouse
source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "naga_ctl.py")).read()
assert "MAX_RUN = 180" in source and 'if __name__ == "__main__":' in source
source = source.replace('if __name__ == "__main__":', 'control_nodes = lambda: {}\nif __name__ == "__main__":')
out = subprocess.run([sys.executable, "-I", "-c", source, "set-button", str(SIDE1), "0"], input=json.dumps(ctrl1) + "\n", capture_output=True, text=True, timeout=30)
assert out.returncode == 0 and json.loads(out.stdout)["connected"] is False, out
with c.mouse_lock():
    stuck = subprocess.run([sys.executable, "-I", "-c", source.replace("MAX_RUN = 180", "MAX_RUN = 1"), "state"], capture_output=True, timeout=30)
assert stuck.returncode == -14 and not stuck.stdout, stuck
print("all backend checks passed")
