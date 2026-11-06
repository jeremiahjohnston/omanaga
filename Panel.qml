import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.jeremiahjohnston.omanaga"
  ipcTarget: "io.github.jeremiahjohnston.omanaga"

  readonly property string script: decodeURIComponent(Qt.resolvedUrl("naga_ctl.py").toString().replace("file://", ""))
  readonly property string setupPath: decodeURIComponent(Qt.resolvedUrl("setup.sh").toString().replace("file://", ""))
  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color selectedFill: bar ? Style.selectedFillFor(bar.foreground, Commons.Color.accent) : "#333"
  readonly property color hoverFill: bar ? Style.hoverFillFor(bar.foreground, Commons.Color.accent) : "#222"
  readonly property int capRadius: Math.max(Style.cornerRadius, Style.space(6))

  // Commons.Color only keeps the accent and urgent colors, so the rest come from the same theme file.
  property var themeColors: ({})
  readonly property color good: themeColor("green", 2, 0.33)
  readonly property color warn: themeColor("yellow", 3, 0.16)
  readonly property color themeMagenta: themeColor("magenta", 5, 0.83)
  readonly property color themeCyan: themeColor("cyan", 6, 0.5)
  readonly property color kindMouse: apartFromAccent(good, themeCyan)
  readonly property color kindMedia: apartFromAccent(themeMagenta, themeCyan)
  readonly property color kindDpi: apartFromAccent(warn, themeCyan)
  readonly property color batteryColor: battery <= lowBattery ? urgent : battery <= 40 ? warn : good
  readonly property color statusColor: !connected ? dim : !permission ? urgent : !loaded && !busy ? warn : good
  readonly property bool isReady: reachable && loaded

  property var st: ({})
  property bool connected: false
  property bool permission: true
  property int battery: -1
  property string message: ""
  property bool messageIsError: false
  property bool messageNearLayouts: false
  property string pendingOk: ""
  property string pendingCmd: ""
  property string pendingInput: ""
  property var queue: []
  readonly property bool loaded: st.buttons !== undefined
  property bool busy: false
  property bool procStarted: false
  property bool procExited: false
  property bool streamFinished: false
  readonly property bool reachable: connected && permission

  property string editLayer: "normal"
  property int selectedId: -1
  property string edKind: "key"
  property string edKey: "1"
  property var edMods: []
  property string edValue: ""
  property string edLabel: ""
  property bool edUnknown: false
  property var draftStages: []
  property string draftActive: "3"
  property int draftDpi: 1600
  property bool confirmingFactory: false
  property string confirmingDelete: ""
  property string confirmingReplace: ""
  readonly property int lowBattery: 15
  // Which side plate is shown. Only the Naga V2 Pro has more than one, and it keeps a map for each.
  property int plate: 12
  readonly property bool hasTopButtons: buttonById(0x0b) !== null
  // The HyperSpeed's top buttons, or the V2 Pro's two behind the wheel.
  readonly property var topIds: hasTopButtons ? [0x0b, 0x0c] : [0x6d, 0x60]
  // Kept from every reply, even a failed one, so a sleeping mouse can still be switched away from.
  property var mice: []
  property string currentMouse: ""

  readonly property var selectedButton: {
    var b = st.buttons || []
    for (var i = 0; i < b.length; i++) if (b[i].id === selectedId) return b[i]
    return null
  }
  readonly property var sideButtons: (st.buttons || []).filter(function(b) { return b.plate === root.plate })
  readonly property var hypershiftKeys: (st.buttons || []).filter(function(b) { return b.normal.kind === "hypershift" })
  readonly property var strandedKeys: (st.buttons || []).filter(function(b) { return b.normal.kind === "hypershift" && !b.canBeKey })
  readonly property var valueOptions: edKind === "mouse" ? [
      { value: "left", label: "Left click" }, { value: "right", label: "Right click" },
      { value: "middle", label: "Middle click" }, { value: "back", label: "Back" }, { value: "forward", label: "Forward" },
      { value: "scrollleft", label: "Scroll left" }, { value: "scrollright", label: "Scroll right" }
    ] : edKind === "dpi" ? [
      { value: "up", label: "DPI up" }, { value: "down", label: "DPI down" }
    ] : edKind === "media" ? [
      { value: "play", label: "Play/Pause" }, { value: "next", label: "Next track" }, { value: "prev", label: "Previous track" },
      { value: "mute", label: "Mute" }, { value: "volup", label: "Volume up" }, { value: "voldown", label: "Volume down" }
    ] : []

  function parseThemeColors(raw) {
    var found = {}
    var lines = String(raw || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var match = lines[i].match(/^\s*([A-Za-z0-9_]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
      if (match) found[match[1]] = match[2]
    }
    return found
  }
  // A theme without this color gets the accent turned around the color wheel instead.
  function themeColor(name, number, turn) {
    var found = themeColors[name] || themeColors["color" + number]
    if (found) return found
    var a = Commons.Color.accent
    return Qt.hsla((Math.max(a.hslHue, 0) + turn) % 1, Math.max(a.hslSaturation, 0.45), a.hslLightness, 1)
  }
  // Key caps use the accent, so a theme whose accent is this color needs another one.
  function apartFromAccent(c, other) {
    var a = Commons.Color.accent
    return Math.abs(c.r - a.r) + Math.abs(c.g - a.g) + Math.abs(c.b - a.b) < 0.25 ? other : c
  }
  function tinted(c, alpha) {
    return Qt.rgba(c.r, c.g, c.b, alpha)
  }
  function buttonById(id) {
    var b = st.buttons || []
    for (var i = 0; i < b.length; i++) if (b[i].id === id) return b[i]
    return null
  }
  function kindColor(kind) {
    return kind === "key" ? Commons.Color.accent : kind === "mouse" ? kindMouse : kind === "media" ? kindMedia
      : kind === "dpi" ? kindDpi : kind === "hypershift" ? urgent : dim
  }
  function kindTag(kind) {
    return { key: "KEY", mouse: "MOUSE", media: "MEDIA", dpi: "DPI", hypershift: "HYPER", disabled: "OFF", factory: "STOCK" }[kind] || "?"
  }
  // Older shells have no Style.duration, and a missing Style.reduceMotion just means animate.
  function motion(ms) {
    return Style.reduceMotion ? 0 : ms
  }

  function run(args, okMessage, input) {
    queue = queue.concat([{ args: args, ok: okMessage || "", input: input || "" }])
    next()
  }
  readonly property bool pendingIsBackground: pendingCmd === "battery"
  readonly property bool working: busy && !pendingIsBackground
  function next() {
    if (busy || queue.length === 0) return
    var item = queue[0]
    queue = queue.slice(1)
    pendingOk = item.ok
    pendingCmd = item.args[0]
    pendingInput = item.input || ""
    procStarted = procExited = streamFinished = false
    busy = true
    proc.command = ["python3", "-I", script].concat(item.args)
    proc.running = true
  }
  // The exited and stream-finished signals come in no guaranteed order, and the output is only complete after both.
  function finishIfDone() {
    if (!busy || !procExited || !streamFinished) return
    busy = false
    var wasReachable = reachable
    handle(out.text)
    if (!wasReachable && reachable && opened && pendingIsBackground) queue = queue.concat([{ args: ["state"], ok: "" }])  // plugged back in
    Qt.callLater(next)
  }
  // A program that can't start sends neither signal. Running just goes false.
  function startFailed() {
    busy = false
    if (!pendingIsBackground) showResult("Couldn't start the backend (python3).", true)
    Qt.callLater(next)
  }
  function handle(text) {
    var r
    try { r = JSON.parse(text) } catch (e) {
      if (!pendingIsBackground) show("The backend returned something unreadable.", true)
      return
    }
    connected = !!r.connected
    permission = r.permission !== false
    if (r.mice !== undefined) {
      mice = r.mice
      currentMouse = r.mouse || ""
      if (!r.ok && loaded && r.mouse !== st.mouse) {  // switched to a mouse that isn't answering: don't show the other one's settings
        st = ({})
        selectedId = -1
        editLayer = "normal"
      }
    }
    if (r.battery !== undefined) {
      battery = r.battery
      if (r.alert) Quickshell.execDetached(["notify-send", "-a", "Omanaga", "-i", "input-mouse", "--",
                                            "Mouse battery low", (r.mouseName || "Mouse") + " battery is at " + Math.round(battery) + "%. " + (r.batteryNote || "")])
    }
    if (!r.ok) {
      if (pendingIsBackground) return  // a sleeping mouse isn't worth a red message
      if (connected && permission) showResult(r.error, true)
      else message = ""
      return
    }
    if (r.buttons) {
      // Reset only what this command changed, so unsaved edits elsewhere in the panel survive.
      var switched = loaded && r.mouse !== st.mouse
      var all = !loaded || switched || ["state", "select-mouse", "apply-layout", "preset"].indexOf(pendingCmd) >= 0
      if (switched) {  // the other mouse has other buttons, so nothing chosen on this one carries over
        selectedId = -1
        editLayer = "normal"
      }
      if (!(r.plates || []).some(function(p) { return p.buttons === plate })) plate = 12
      st = r
      if (all || pendingCmd === "set-dpi") draftStages = r.stages.slice()
      if (all || pendingCmd === "set-dpi" || pendingCmd === "set-current-dpi") {
        draftDpi = r.dpi
        draftActive = r.dpiLevel ? String(r.dpiLevel) : ""  // no level is highlighted on a custom DPI
      }
      if (!r.hasHypershift) editLayer = "normal"
      if (selectedId >= 0 && (all || pendingCmd === "set-button")) loadEditor()
    } else if (loaded && (r.idle !== undefined || r.layouts !== undefined)) {  // saved, but the mouse is too asleep to refresh
      var kept = {}
      if (r.idle !== undefined) kept.idle = r.idle
      if (r.layouts !== undefined) kept.layouts = r.layouts
      st = Object.assign({}, st, kept)
    }
    if (pendingCmd === "save-layout") layoutName.text = ""  // kept until now so a failed save doesn't lose it
    if (r.warning) showResult(r.warning, false)
    else if (pendingOk) showResult(pendingOk, false)
  }
  // Never piles up: a poll is skipped while another is waiting or running.
  function pollBattery() {
    if (busy && pendingIsBackground) return
    if (queue.some(function(q) { return q.args[0] === "battery" })) return
    run(["battery"])
  }
  function saveLayout() {
    var name = layoutName.text.trim()
    if (name === "") {
      show("Type a name for this layout first.", true)
      messageNearLayouts = true
      return
    }
    if ((st.layouts || []).indexOf(name) >= 0) {
      confirmingReplace = name  // stays until Replace or Cancel is clicked, or the name is edited
      return
    }
    saveLayoutNamed(name)
  }
  function saveLayoutNamed(name) {
    confirmingReplace = ""
    run(["save-layout", name], "Saved layout " + name)
    layoutName.focus = false
  }
  function deleteClicked(name) {
    if (confirmingDelete !== name) {
      confirmingDelete = name
      deleteTimer.restart()
      return
    }
    confirmingDelete = ""
    run(["delete-layout", name], "Deleted layout " + name)
  }
  function show(text, isError) {
    message = text
    messageIsError = !!isError
    messageNearLayouts = false
    messageTimer.restart()
  }
  // The result of a command. Layout commands also show it in the Saved layouts section.
  function showResult(text, isError) {
    show(text, isError)
    messageNearLayouts = ["save-layout", "apply-layout", "delete-layout"].indexOf(pendingCmd) >= 0
  }
  function select(id) {
    selectedId = selectedId === id ? -1 : id
    if (selectedId >= 0) loadEditor()
  }
  function setLayer(v) {
    editLayer = v
    if (v === "hypershift" && selectedButton && !selectedButton.layerEditable) selectedId = -1
    if (selectedId >= 0) loadEditor()
  }
  function loadEditor() {
    var b = selectedButton
    if (!b) return
    var a = editLayer === "hypershift" ? b.hypershift : b.normal
    edLabel = a.label
    // Something the pickers can't show stays as it is until the user chooses something else.
    edUnknown = a.kind === "custom" || (a.kind === "key" && !a.key) || (["mouse", "dpi", "media"].indexOf(a.kind) >= 0 && !a.value)
    edKind = edUnknown ? "custom" : a.kind
    edKey = a.key || "1"
    edMods = a.mods || []
    edValue = a.value || (edKind === "mouse" ? "left" : edKind === "dpi" ? "up" : edKind === "media" ? "play" : "")
  }
  function toggleMod(m) {
    edMods = edMods.indexOf(m) >= 0 ? edMods.filter(function(x) { return x !== m }) : edMods.concat([m])
  }
  function setKind(k) {
    edKind = k
    edValue = k === "mouse" ? "left" : k === "dpi" ? "up" : k === "media" ? "play" : ""
  }
  function saveButton() {
    if (edKind === "custom") {
      show("Choose what this button should do first. It stays as it is until then.", true)
      return
    }
    var action = { kind: edKind }
    if (edKind === "key") { action.key = edKey; action.mods = edMods }
    else if (edValue) action.value = edValue
    run(["set-button", String(selectedId), editLayer === "hypershift" ? "1" : "0"],
        selectedButton.name + " saved to the mouse", JSON.stringify(action))
  }
  // A typed number only reaches the field's value on Enter or focus loss, and clicking a
  // button does neither, so read what's on screen when saving.
  function typedValue(numberField) {
    var box = numberField.field
    var v = box.valueFromText(box.contentItem.text, box.locale)
    if (isNaN(v)) return box.value
    return Math.max(box.from, Math.min(box.to, v))
  }
  function saveCurrentDpi() {
    draftDpi = typedValue(currentField)
    run(["set-current-dpi", String(draftDpi)], "DPI set to " + draftDpi)
  }
  function saveDpi() {
    var s = []
    for (var i = 0; i < 5; i++) s.push(typedValue(levelFields.itemAt(i)))
    draftStages = s
    run(["set-dpi", s.join(","), draftActive || "keep"], "DPI levels saved to the mouse")
  }
  function setStage(i, v) {
    var s = draftStages.slice()
    s[i] = v
    draftStages = s
  }
  function factoryClicked() {
    if (!confirmingFactory) {
      confirmingFactory = true
      factoryTimer.restart()
      return
    }
    confirmingFactory = false
    selectedId = -1
    run(["preset", "factory"], "Factory settings restored")
  }

  onOpenedChanged: if (opened) run(["state"])
  Component.onCompleted: run(["battery"])

  Timer { id: messageTimer; interval: 6000; onTriggered: root.message = "" }
  Timer { id: factoryTimer; interval: 4000; onTriggered: root.confirmingFactory = false }
  Timer { id: deleteTimer; interval: 4000; onTriggered: root.confirmingDelete = "" }
  Timer { interval: root.reachable ? 600000 : 10000; running: true; repeat: true; onTriggered: root.pollBattery() }

  Process {
    id: proc
    stdinEnabled: true  // a button's action goes over stdin, not the command line
    stdout: StdioCollector {
      id: out
      onStreamFinished: {
        root.streamFinished = true
        root.finishIfDone()
      }
    }
    onStarted: {
      root.procStarted = true
      if (root.pendingInput) write(root.pendingInput + "\n")
    }
    onExited: {
      root.procExited = true
      root.finishIfDone()
    }
    onRunningChanged: if (!running && root.busy && !root.procStarted) root.startFailed()
  }

  FileView {
    id: themeFile
    path: Commons.Color.currentThemePath + "/colors.toml"
    printErrors: false
    onLoaded: root.themeColors = root.parseThemeColors(text())
    onLoadFailed: root.themeColors = ({})
  }
  // A theme switch reaches the shell over IPC, so the file's own change signal can't be relied on.
  Connections {
    target: Commons.Color
    function onAccentChanged() { themeFile.reload() }
    function onBackgroundChanged() { themeFile.reload() }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\u{F037D}"
    opacity: root.connected ? 1 : 0.5
    foreground: root.connected && root.battery >= 0 && root.battery <= root.lowBattery ? root.urgent : root.barForeground
    tooltipText: !root.connected ? "Naga: receiver not found"
      : root.battery >= 0 ? (root.st.mouseName || "Naga") + ": battery " + root.battery + "%" : (root.st.mouseName || "Naga")
    onPressed: function(b) { root.toggle() }
  }

  component SectionHeader: Item {
    id: sectionHeader
    property string text: ""
    width: parent ? parent.width : 0
    height: headerLabel.implicitHeight
    PanelSectionHeader {
      id: headerLabel
      width: Math.min(implicitWidth, sectionHeader.width)
      text: sectionHeader.text.toUpperCase()
      foreground: root.foreground
      fontFamily: root.fontFamily
      font.letterSpacing: 1.2
      elide: Text.ElideRight
    }
    PanelSeparator {
      x: headerLabel.width + Style.space(8)
      width: Math.max(0, sectionHeader.width - x)
      anchors.verticalCenter: headerLabel.verticalCenter
      foreground: root.foreground
    }
  }

  component BatteryGauge: Item {
    id: gauge
    property int level: 0
    width: Style.space(26)
    height: Style.space(11)
    Rectangle {
      id: gaugeBody
      width: parent.width - Style.space(2)
      height: parent.height
      radius: Style.space(2)
      color: "transparent"
      border.width: 1
      border.color: root.dim
      Rectangle {
        x: Style.space(2)
        y: Style.space(2)
        width: Math.max(0, (gaugeBody.width - Style.space(4)) * Math.min(gauge.level, 100) / 100)
        height: gaugeBody.height - Style.space(4)
        radius: 1
        color: root.batteryColor
        Behavior on width { NumberAnimation { duration: root.motion(200) } }
        Behavior on color { ColorAnimation { duration: root.motion(200) } }
      }
    }
    Rectangle {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(2)
      height: Style.space(5)
      color: root.dim
    }
  }

  component StatTile: Rectangle {
    id: tile
    property string label: ""
    property string value: ""
    property string note: ""
    property color valueColor: root.foreground
    property bool showGauge: false
    height: tileColumn.implicitHeight + Style.space(14)
    radius: root.capRadius
    color: root.tinted(root.foreground, 0.05)
    border.width: 1
    border.color: root.tinted(root.foreground, 0.12)
    Column {
      id: tileColumn
      x: Style.space(9)
      width: parent.width - Style.space(18)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)
      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: tile.label.toUpperCase()
        color: root.dim
        elide: Text.ElideRight
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
      }
      Row {
        width: parent.width
        spacing: Style.space(6)
        BatteryGauge {
          id: tileGauge
          visible: tile.showGauge
          level: root.battery
          anchors.verticalCenter: parent.verticalCenter
        }
        Text {
          width: parent.width - (tile.showGauge ? tileGauge.width + parent.spacing : 0)
          height: implicitHeight
          textFormat: Text.PlainText
          text: tile.value
          color: tile.valueColor
          elide: Text.ElideRight
          fontSizeMode: Text.HorizontalFit
          minimumPixelSize: Style.font.caption
          font.family: root.fontFamily
          font.pixelSize: Style.font.heading
          font.bold: true
        }
      }
      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: tile.note
        color: root.dim
        elide: Text.ElideRight
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // A key cap: the colored edge is a second shape showing below the face, so it follows the rounded corners.
  component Cell: Item {
    id: cell
    property var button: null
    readonly property var shownAction: button ? (root.editLayer === "hypershift" ? button.hypershift : button.normal) : null
    readonly property string cellKind: shownAction ? shownAction.kind : ""
    readonly property bool isActive: !!button && root.selectedId === button.id
    readonly property bool locked: !!button && root.editLayer === "hypershift" && !button.layerEditable
    readonly property bool stranded: !!button && button.normal.kind === "hypershift" && !button.canBeKey
    readonly property color capColor: root.kindColor(cellKind)
    readonly property bool hot: cellHover.hovered && !locked
    opacity: locked ? 0.45 : 1
    height: Style.space(50)
    Item {
      width: parent.width
      height: parent.height
      y: cell.hot ? -1 : 0
      Behavior on y { NumberAnimation { duration: root.motion(100) } }
      Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: root.capRadius * 2 + Style.space(3)
        radius: root.capRadius
        color: cell.capColor
        Behavior on color { ColorAnimation { duration: root.motion(150) } }
      }
      Rectangle {
        anchors.fill: parent
        anchors.bottomMargin: Style.space(3)
        radius: root.capRadius
        color: Qt.tint(Commons.Color.background, root.tinted(cell.isActive ? cell.capColor : root.foreground, cell.isActive ? 0.24 : cell.hot ? 0.14 : 0.07))
        border.width: 1
        border.color: cell.isActive ? cell.capColor : root.tinted(root.foreground, 0.16)
        Behavior on color { ColorAnimation { duration: root.motion(120) } }
        Behavior on border.color { ColorAnimation { duration: root.motion(120) } }
        Column {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.space(7)
          anchors.rightMargin: Style.space(7)
          spacing: Style.space(2)
          Item {
            width: parent.width
            height: nameText.implicitHeight
            Text {
              id: nameText
              width: parent.width - tagText.implicitWidth - Style.space(4)
              textFormat: Text.PlainText
              text: cell.button ? cell.button.name : ""
              color: root.dim
              elide: Text.ElideRight
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            Text {
              id: tagText
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: root.kindTag(cell.cellKind)
              color: cell.capColor
              font.family: root.fontFamily
              font.pixelSize: Math.max(1, Style.font.caption - 1)
              font.bold: true
            }
          }
          Text {
            width: parent.width
            height: implicitHeight
            textFormat: Text.PlainText
            text: cell.shownAction ? cell.shownAction.label : ""
            color: root.foreground
            elide: Text.ElideRight
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: Style.font.caption
            font.family: root.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
        }
      }
    }
    Rectangle {
      visible: cell.stranded
      anchors.fill: parent
      anchors.margins: -1
      radius: root.capRadius + 1
      color: "transparent"
      border.width: 2
      border.color: root.urgent
    }
    HoverHandler { id: cellHover; enabled: !cell.locked; cursorShape: Qt.PointingHandCursor }
    TapHandler { enabled: !cell.locked; onTapped: if (cell.button) root.select(cell.button.id) }
  }

  // The glow is stacked see-through borders, so it stays inside the card and the panel can't clip it.
  component Card: Rectangle {
    id: card
    property bool glowing: false
    readonly property int inset: Style.space(14)
    width: parent ? parent.width : 0
    radius: root.capRadius + Style.space(2)
    color: root.tinted(root.foreground, 0.03)
    border.width: 1
    border.color: glowing ? root.urgent : root.tinted(root.foreground, 0.12)
    Behavior on border.color { ColorAnimation { duration: root.motion(250) } }
    Item {
      anchors.fill: parent
      opacity: card.glowing ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: root.motion(250) } }
      Repeater {
        model: 4
        Rectangle {
          required property int index
          anchors.fill: parent
          radius: card.radius
          color: "transparent"
          border.width: Style.space((index + 1) * 3)
          border.color: root.tinted(root.urgent, 0.08)
        }
      }
    }
  }

  component SubLabel: Text {
    width: parent ? parent.width : 0
    textFormat: Text.PlainText
    color: root.dim
    elide: Text.ElideRight
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    font.letterSpacing: 1
  }

  // The stock dropdowns assign their own value when you pick an option, which ends a plain
  // `value:` binding. These follow boundValue, so they keep up when another button is selected.
  component Picker: Dropdown {
    property string boundValue: ""
    value: boundValue
    onBoundValueChanged: value = boundValue
    width: parent ? parent.width : 0
    showLabel: false
    foreground: root.foreground
    fontFamily: root.fontFamily
  }
  component KeyPicker: SearchableDropdown {
    property string boundValue: ""
    value: boundValue
    onBoundValueChanged: value = boundValue
    width: parent ? parent.width : 0
    showLabel: false
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  component MessageStrip: Rectangle {
    id: strip
    readonly property color edgeColor: root.messageIsError ? root.urgent : root.good
    readonly property string shownMessage: root.message
    onShownMessageChanged: if (shownMessage !== "") stripFade.restart()
    visible: root.message !== ""
    height: stripText.implicitHeight + Style.space(16)
    radius: root.capRadius
    color: root.tinted(edgeColor, 0.1)
    border.width: 1
    border.color: root.tinted(edgeColor, 0.35)
    NumberAnimation { id: stripFade; target: strip; property: "opacity"; from: 0; to: 1; duration: root.motion(180) }
    Rectangle {
      x: Style.space(5)
      width: Style.space(3)
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      anchors.topMargin: Style.space(5)
      anchors.bottomMargin: Style.space(5)
      radius: width / 2
      color: strip.edgeColor
    }
    Text {
      x: Style.space(14)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root.messageIsError ? "!" : "\u2713"
      color: strip.edgeColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }
    Text {
      id: stripText
      anchors.verticalCenter: parent.verticalCenter
      x: Style.space(28)
      width: strip.width - x - Style.space(8)
      wrapMode: Text.Wrap
      textFormat: Text.PlainText
      text: root.message
      color: root.messageIsError ? root.urgent : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }
  }

  component Info: Text {
    width: parent ? parent.width : 0
    wrapMode: Text.Wrap
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(1180))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(1500))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: stageRepeater.anyFocused || layoutName.activeFocus || kindPicker.popupOpen || keyPicker.popupOpen || valuePicker.popupOpen
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: flick.width
          spacing: Style.space(12)

          Item {
            width: parent.width
            height: titleRow.implicitHeight
            Row {
              id: titleRow
              spacing: Style.space(10)
              Rectangle {
                width: Style.space(9)
                height: Style.space(9)
                radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                color: root.statusColor
                Behavior on color { ColorAnimation { duration: root.motion(200) } }
              }
              Text {
                id: titleText
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: root.st.mouseName || (root.mice.filter(function(m) { return m.id === root.currentMouse })[0] || { name: "Naga V2 HyperSpeed" }).name
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                font.bold: true
              }
              Text {
                visible: root.reachable && !!root.st.firmware
                anchors.baseline: titleText.baseline
                textFormat: Text.PlainText
                text: "firmware " + (root.st.firmware || "")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Repeater {
                // Both mice plugged in: pick which one this panel shows. The pick is remembered.
                model: root.mice.length > 1 ? root.mice : []
                Button {
                  required property var modelData
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.name.replace("Naga V2 ", "")
                  bordered: true
                  selected: root.currentMouse === modelData.id
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: if (!selected) root.run(["select-mouse", modelData.id])
                }
              }
            }
            Text {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: root.working && root.connected ? "Talking to the mouse..." : !root.connected ? "Not connected" : !root.permission ? "Setup needed" : !root.loaded ? "Not answering" : "Connected"
              color: root.working ? Commons.Color.accent : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Row {
            id: statRow
            visible: root.isReady
            width: parent.width
            spacing: Style.space(8)
            readonly property real tileWidth: (width - spacing * 5) / 6
            StatTile {
              width: statRow.tileWidth
              label: "Battery"
              showGauge: root.battery >= 0
              value: root.battery >= 0 ? root.battery + "%" : "?"
              valueColor: root.battery >= 0 && root.battery <= root.lowBattery ? root.urgent : root.foreground
              note: root.st.mouse === "pro" ? "rechargeable" : "1 x AA"
            }
            StatTile {
              width: statRow.tileWidth
              label: "DPI"
              value: String(root.st.dpi)
              note: root.st.dpiLevel === 0 ? "custom" : "level " + root.st.dpiLevel
            }
            StatTile {
              width: statRow.tileWidth
              label: "Polling"
              value: root.st.poll + " Hz"
              note: 1000 / root.st.poll + " ms"
            }
            StatTile {
              width: statRow.tileWidth
              label: "Sleep"
              value: root.st.idle % 60 === 0 ? root.st.idle / 60 + " min" : root.st.idle + " s"
              note: "idle timer"
            }
            StatTile {
              width: statRow.tileWidth
              label: "Hypershift"
              value: root.hypershiftKeys.length > 0 ? root.hypershiftKeys.map(function(b) { return b.name.replace("Top button", "Top") }).join(", ") : "None"
              valueColor: root.hypershiftKeys.length > 0 ? root.urgent : root.foreground
              note: root.strandedKeys.length > 0 ? "won't work there" : root.hypershiftKeys.length > 0 ? "hold for the other layer" : "no key set"
            }
            StatTile {
              width: statRow.tileWidth
              label: "Layout"
              value: root.st.loadedLayout || "None"
              note: root.st.loadedLayout ? "on the mouse" : "no saved match"
            }
          }

          Item {
            id: sweep
            width: parent.width
            height: Style.space(2)
            clip: true
            opacity: root.working ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: root.motion(150) } }
            Rectangle {
              id: sweepBar
              width: Style.reduceMotion ? sweep.width : sweep.width * 0.35
              height: sweep.height
              gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: root.tinted(Commons.Color.accent, 0) }
                GradientStop { position: 0.5; color: Commons.Color.accent }
                GradientStop { position: 1; color: root.tinted(Commons.Color.accent, 0) }
              }
              NumberAnimation on x {
                from: -sweepBar.width
                to: sweep.width
                duration: root.motion(1200)
                loops: Animation.Infinite
                running: root.working && root.opened && !Style.reduceMotion
              }
            }
          }

          MessageStrip { width: parent.width }

          Column {
            id: notReady
            visible: !root.isReady
            readonly property bool asleep: root.reachable && !root.busy
            readonly property color stateColor: !root.connected ? root.dim : !root.permission ? root.urgent : root.warn
            width: parent.width
            topPadding: Style.space(24)
            bottomPadding: Style.space(24)
            spacing: Style.space(10)
            Rectangle {
              id: mouseOutline
              anchors.horizontalCenter: parent.horizontalCenter
              width: Style.space(56)
              height: Style.space(80)
              radius: Style.space(26)
              color: "transparent"
              border.width: 2
              border.color: notReady.stateColor
              Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: Style.space(12)
                width: Style.space(4)
                height: Style.space(14)
                radius: width / 2
                color: notReady.stateColor
              }
              // Only a person moving the mouse wakes it, so this shows them what to do.
              SequentialAnimation on anchors.horizontalCenterOffset {
                running: notReady.asleep && notReady.visible && root.opened && !Style.reduceMotion
                loops: Animation.Infinite
                alwaysRunToEnd: true
                NumberAnimation { to: -5; duration: root.motion(220) }
                NumberAnimation { to: 5; duration: root.motion(440) }
                NumberAnimation { to: 0; duration: root.motion(220) }
                PauseAnimation { duration: root.motion(1300) }
              }
            }
            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              textFormat: Text.PlainText
              text: !root.connected ? "Mouse receiver not found" : !root.permission ? "One setup step needed" : root.busy ? "Reading the mouse..." : "The mouse is asleep or not answering"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Info {
              horizontalAlignment: Text.AlignHCenter
              visible: text !== ""
              text: !root.connected ? "Plug in the Naga V2 HyperSpeed's USB dongle or the Naga V2 Pro's Mouse Dock Pro, and set the mouse to 2.4 GHz. Settings can't be changed over Bluetooth."
                : !root.permission ? "So this panel can talk to the mouse, run this once in a terminal. It shows what it will do and asks first:"
                : root.busy ? "" : "Move the mouse to wake it, then try again. Commands alone can't wake it."
              font.pixelSize: Style.font.body
            }
            Rectangle {
              visible: root.connected && !root.permission
              anchors.horizontalCenter: parent.horizontalCenter
              width: Math.min(setupCommand.implicitWidth + Style.space(20), parent.width)
              height: setupCommand.implicitHeight + Style.space(10)
              radius: root.capRadius
              color: root.tinted(root.foreground, 0.06)
              border.width: 1
              border.color: root.tinted(root.foreground, 0.14)
              Text {
                id: setupCommand
                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - Style.space(20))
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
                text: "sudo " + root.setupPath
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
            }
            Button {
              anchors.horizontalCenter: parent.horizontalCenter
              text: "Try again"
              bordered: true
              foreground: root.foreground
              onClicked: root.run(["state"])
            }
          }

          Row {
            id: mainRow
            visible: root.isReady
            width: parent.width
            spacing: Style.space(14)
            readonly property real leftWidth: (width - spacing) * 1.3 / 2.3

            Column {
              width: mainRow.leftWidth
              spacing: Style.space(14)

              Card {
                glowing: root.editLayer === "hypershift"
                height: buttonsColumn.implicitHeight + inset * 2
                Column {
                  id: buttonsColumn
                  x: parent.inset
                  y: parent.inset
                  width: parent.width - parent.inset * 2
                  spacing: Style.space(12)

                  Item {
                    width: parent.width
                    height: Math.max(buttonsHeader.height, layerToggle.implicitHeight)
                    SectionHeader {
                      id: buttonsHeader
                      text: "Buttons"
                      width: parent.width - (layerToggle.visible ? layerToggle.implicitWidth + Style.space(8) : 0)
                      anchors.verticalCenter: parent.verticalCenter
                    }
                    Row {
                      id: layerToggle
                      visible: !!root.st.hasHypershift
                      anchors.right: parent.right
                      spacing: Style.space(6)
                      Repeater {
                        model: [{ value: "normal", label: "Normal" }, { value: "hypershift", label: "While holding Hypershift" }]
                        Button {
                          required property var modelData
                          text: modelData.label
                          bordered: true
                          selected: root.editLayer === modelData.value
                          foreground: selected && modelData.value === "hypershift" ? root.urgent : root.foreground
                          fontFamily: root.fontFamily
                          onClicked: root.setLayer(modelData.value)
                        }
                      }
                    }
                  }

                  Row {
                    id: board
                    width: parent.width
                    spacing: Style.space(14)
                    readonly property real sideWidth: (width - spacing) * 1.4 / 2.4

                    Column {
                      width: board.sideWidth
                      spacing: Style.space(6)
                      Item {
                        width: parent.width
                        height: Math.max(sideLabel.implicitHeight, plateToggle.implicitHeight)
                        SubLabel { id: sideLabel; text: "SIDE PANEL"; anchors.verticalCenter: parent.verticalCenter }
                        Row {
                          id: plateToggle
                          visible: (root.st.plates || []).length > 1
                          anchors.right: parent.right
                          spacing: Style.space(4)
                          Repeater {
                            model: root.st.plates || []
                            Button {
                              required property var modelData
                              text: String(modelData.buttons)
                              bordered: true
                              selected: root.plate === modelData.buttons
                              foreground: root.foreground
                              fontFamily: root.fontFamily
                              onClicked: {
                                root.plate = modelData.buttons
                                if (root.selectedButton && root.selectedButton.plate && root.selectedButton.plate !== root.plate) root.selectedId = -1
                              }
                            }
                          }
                        }
                      }
                      Grid {
                        id: sideGrid
                        width: parent.width
                        columns: 3
                        spacing: Style.space(6)
                        readonly property real cellWidth: (width - spacing * 2) / 3
                        Repeater {
                          model: root.sideButtons
                          Cell {
                            required property var modelData
                            width: sideGrid.cellWidth
                            button: modelData
                          }
                        }
                      }
                    }

                    Column {
                      id: topColumn
                      width: board.width - board.sideWidth - board.spacing
                      spacing: Style.space(6)
                      readonly property real halfWidth: (width - spacing) / 2
                      SubLabel { text: "TOP AND WHEEL" }
                      Row {
                        spacing: topColumn.spacing
                        Cell { width: topColumn.halfWidth; button: root.buttonById(0x34) }
                        Cell { width: topColumn.halfWidth; button: root.buttonById(0x35) }
                      }
                      Cell { width: topColumn.width; button: root.buttonById(0x03) }
                      Row {
                        visible: root.buttonById(root.topIds[0]) !== null
                        spacing: topColumn.spacing
                        Cell { width: topColumn.halfWidth; button: root.buttonById(root.topIds[0]) }
                        Cell { width: topColumn.halfWidth; button: root.buttonById(root.topIds[1]) }
                      }
                      Cell { visible: button !== null; width: topColumn.width; button: root.buttonById(0x0e) }
                      Info {
                        text: root.editLayer === "hypershift" ? "Holding Hypershift only changes the side buttons."
                          : root.hasTopButtons ? "Click any button to choose what it does. The two top buttons don't have to change DPI, and only they can be the Hypershift key."
                          : (root.st.plates || []).length > 1 ? "Click any button to choose what it does. The mouse keeps a map for each side plate, so pick the plate that's fitted. Any side button or the wheel click can be the Hypershift key."
                          : "Click any button to choose what it does."
                      }
                    }
                  }

                  Flow {
                    width: parent.width
                    spacing: Style.space(10)
                    Repeater {
                      model: [{ kind: "key", label: "Key" }, { kind: "mouse", label: "Mouse" }, { kind: "media", label: "Media" }, { kind: "dpi", label: "DPI" }, { kind: "hypershift", label: "Hypershift" }, { kind: "disabled", label: "Off" }]
                      Row {
                        required property var modelData
                        spacing: Style.space(4)
                        Rectangle {
                          width: Style.space(8)
                          height: Style.space(8)
                          radius: Style.space(2)
                          anchors.verticalCenter: parent.verticalCenter
                          color: root.kindColor(modelData.kind)
                        }
                        Text {
                          textFormat: Text.PlainText
                          text: modelData.label
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                        }
                      }
                    }
                  }

                  Info {
                    visible: root.strandedKeys.length > 0
                    text: "The Hypershift key on " + root.strandedKeys.map(function(b) { return b.name }).join(", ") + " won't work, because only the top buttons can be the key. Change it on the Normal layer."
                    color: root.urgent
                    font.pixelSize: Style.font.body
                  }

                  PanelSeparator { width: parent.width; foreground: root.foreground }

                  Text {
                    visible: root.selectedButton === null
                    width: parent.width
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: "Pick a button above to change what it does."
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  Column {
                    id: editorColumn
                    visible: root.selectedButton !== null
                    width: parent.width
                    spacing: Style.space(8)
                    readonly property real halfWidth: (width - Style.space(8)) / 2

                    SectionHeader {
                      text: root.selectedButton ? "Change " + root.selectedButton.name + (root.editLayer === "hypershift" ? " while holding Hypershift" : "") : ""
                    }
                    Row {
                      width: parent.width
                      spacing: Style.space(8)
                      Picker {
                        id: kindPicker
                        width: editorColumn.halfWidth
                        boundValue: root.edKind
                        options: [
                          { value: "key", label: "Keyboard key" }, { value: "mouse", label: "Mouse button" },
                          { value: "dpi", label: "DPI" }, { value: "media", label: "Media" }
                        ].concat((root.selectedButton && root.selectedButton.canBeKey) || root.edKind === "hypershift" ? [{ value: "hypershift", label: "Hypershift key" }] : []).concat([
                          { value: "disabled", label: "Disabled" }, { value: "factory", label: "Factory default" }
                        ]).concat(root.edUnknown ? [{ value: "custom", label: "Keep as it is" }] : [])
                        onChanged: function(v) { root.setKind(v) }
                      }
                      KeyPicker {
                        id: keyPicker
                        width: editorColumn.halfWidth
                        visible: root.edKind === "key"
                        boundValue: root.edKey
                        placeholderText: "Search keys, like F13 or Home"
                        options: root.st.keys || []
                        onChanged: function(v) { root.edKey = v }
                      }
                      Picker {
                        id: valuePicker
                        width: editorColumn.halfWidth
                        visible: root.valueOptions.length > 0
                        boundValue: root.edValue
                        options: root.valueOptions
                        onChanged: function(v) { root.edValue = v }
                      }
                    }
                    Info {
                      visible: root.edKind === "custom"
                      text: "Set to " + root.edLabel + ", which this panel can't edit. It stays that way unless you choose something else and save."
                    }
                    Flow {
                      width: parent.width
                      spacing: Style.space(10)
                      Row {
                        visible: root.edKind === "key"
                        spacing: Style.space(6)
                        Repeater {
                          model: [{ m: "ctrl", l: "Ctrl" }, { m: "shift", l: "Shift" }, { m: "alt", l: "Alt" }, { m: "super", l: "Super" }]
                          Button {
                            required property var modelData
                            text: modelData.l
                            bordered: true
                            foreground: root.foreground
                            selected: root.edMods.indexOf(modelData.m) >= 0
                            onClicked: root.toggleMod(modelData.m)
                          }
                        }
                      }
                      Row {
                        spacing: Style.space(6)
                        Button { text: "Save to mouse"; bordered: true; foreground: root.foreground; onClicked: root.saveButton() }
                        Button { text: "Close"; foreground: root.foreground; onClicked: root.selectedId = -1 }
                      }
                    }
                  }
                }
              }

              Card {
                height: deviceColumn.implicitHeight + inset * 2
                Column {
                  id: deviceColumn
                  x: parent.inset
                  y: parent.inset
                  width: parent.width - parent.inset * 2
                  spacing: Style.space(10)
                  readonly property real labelWidth: Style.space(84)

                  SectionHeader { text: "Device" }
                  Row {
                    spacing: Style.space(8)
                    Text {
                      width: deviceColumn.labelWidth
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: "Polling rate"
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                    ButtonGroup {
                      options: [{ value: "125", label: "125 Hz" }, { value: "500", label: "500 Hz" }, { value: "1000", label: "1000 Hz" }]
                      value: String(root.st.poll || "")
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      onChanged: function(v) { root.run(["set-poll", v], "Polling rate saved to the mouse") }
                    }
                  }
                  Row {
                    spacing: Style.space(8)
                    Text {
                      width: deviceColumn.labelWidth
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: "Sleep after"
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                    ButtonGroup {
                      options: [{ value: "60", label: "1 min" }, { value: "120", label: "2 min" }, { value: "300", label: "5 min" }, { value: "600", label: "10 min" }, { value: "900", label: "15 min" }]
                      value: String(root.st.idle || "")
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      onChanged: function(v) { root.run(["set-idle", v], "Sleep timer saved to the mouse") }
                    }
                  }
                  Row {
                    spacing: Style.space(8)
                    Text {
                      width: deviceColumn.labelWidth
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: "Quick actions"
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                    Button {
                      text: "Ctrl+1 to Ctrl+6"
                      tooltipText: "Sets side buttons 1-6 to Ctrl+1 to Ctrl+6. Other buttons don't change."
                      bordered: true
                      foreground: root.foreground
                      onClicked: root.run(["preset", "wow"], "Ctrl+1 to Ctrl+6 saved to the mouse")
                    }
                    Button {
                      text: root.confirmingFactory ? "Click again to reset everything" : "Factory reset"
                      bordered: true
                      foreground: root.confirmingFactory ? root.urgent : root.foreground
                      onClicked: root.factoryClicked()
                    }
                  }
                  Info { text: "The mouse has no lighting to set. Macros aren't included because the mouse can't store them." }
                }
              }
            }

            Column {
              width: mainRow.width - mainRow.leftWidth - mainRow.spacing
              spacing: Style.space(14)

              Card {
                height: dpiColumn.implicitHeight + inset * 2
                Column {
                  id: dpiColumn
                  x: parent.inset
                  y: parent.inset
                  width: parent.width - parent.inset * 2
                  spacing: Style.space(10)

                  SectionHeader { text: "DPI" }
                  Item {
                    width: parent.width
                    height: Math.max(dpiBig.implicitHeight, currentRow.implicitHeight)
                    Row {
                      id: dpiBig
                      anchors.bottom: parent.bottom
                      spacing: Style.space(8)
                      Text {
                        id: dpiNumber
                        textFormat: Text.PlainText
                        text: String(root.st.dpi)
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.displayLarge
                        font.bold: true
                      }
                      Text {
                        anchors.baseline: dpiNumber.baseline
                        textFormat: Text.PlainText
                        text: "DPI"
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                      }
                      Rectangle {
                        visible: root.st.dpiLevel === 0
                        anchors.verticalCenter: dpiNumber.verticalCenter
                        width: customTag.implicitWidth + Style.space(14)
                        height: customTag.implicitHeight + Style.space(4)
                        radius: height / 2
                        color: "transparent"
                        border.width: 1
                        border.color: root.warn
                        Text {
                          id: customTag
                          anchors.centerIn: parent
                          textFormat: Text.PlainText
                          text: "Custom"
                          color: root.warn
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                        }
                      }
                    }
                    Row {
                      id: currentRow
                      anchors.right: parent.right
                      anchors.bottom: parent.bottom
                      spacing: Style.space(8)
                      NumberField {
                        id: currentField
                        label: "Current DPI"
                        from: 100
                        to: 30000
                        stepSize: 50
                        foreground: root.foreground
                        fontFamily: root.fontFamily
                        value: root.draftDpi
                        onModified: function(v) { root.draftDpi = v }
                        Connections {
                          target: currentField.field
                          function onActiveFocusChanged() { stageRepeater.anyFocused = currentField.field.activeFocus }
                        }
                      }
                      Button {
                        text: "Set DPI"
                        anchors.bottom: parent.bottom
                        height: currentField.field.height
                        bordered: true
                        foreground: root.foreground
                        onClicked: root.saveCurrentDpi()
                      }
                    }
                  }
                  Info { text: "The levels are only used if a button is set to DPI up or down. Pressing one jumps to the next level here. Click a bar to choose the level to use, then save." }
                  Row {
                    id: levelBars
                    width: parent.width
                    spacing: Style.space(6)
                    readonly property real barWidth: (width - spacing * 4) / 5
                    readonly property real peak: Math.max.apply(null, root.draftStages.concat([100]))
                    Repeater {
                      model: 5
                      Column {
                        id: levelBar
                        required property int index
                        readonly property int stage: root.draftStages.length > index ? root.draftStages[index] : 100
                        readonly property bool lit: root.draftActive === String(index + 1)
                        width: levelBars.barWidth
                        spacing: Style.space(3)
                        Item {
                          width: parent.width
                          height: Style.space(64)
                          Rectangle {
                            anchors.bottom: parent.bottom
                            width: parent.width
                            height: Math.max(Style.space(4), parent.height * Math.sqrt(levelBar.stage / levelBars.peak))
                            radius: Style.space(4)
                            color: levelBar.lit ? root.kindDpi : root.tinted(root.foreground, barHover.hovered ? 0.2 : 0.12)
                            Behavior on height { NumberAnimation { duration: root.motion(200) } }
                            Behavior on color { ColorAnimation { duration: root.motion(150) } }
                          }
                          HoverHandler { id: barHover; cursorShape: Qt.PointingHandCursor }
                          TapHandler { onTapped: root.draftActive = String(levelBar.index + 1) }
                        }
                        Text {
                          width: parent.width
                          horizontalAlignment: Text.AlignHCenter
                          textFormat: Text.PlainText
                          text: "Level " + (levelBar.index + 1)
                          color: levelBar.lit ? root.foreground : root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          font.bold: levelBar.lit
                        }
                      }
                    }
                  }
                  Row {
                    id: stageRepeater
                    width: parent.width
                    spacing: levelBars.spacing
                    property bool anyFocused: false
                    Repeater {
                      id: levelFields
                      model: 5
                      NumberField {
                        id: levelField
                        required property int index
                        fieldWidth: levelBars.barWidth
                        from: 100
                        to: 30000
                        stepSize: 50
                        foreground: root.foreground
                        fontFamily: root.fontFamily
                        value: root.draftStages.length > index ? root.draftStages[index] : 100
                        onModified: function(v) { root.setStage(index, v) }
                        // At this width some styles' arrows would cover the number. The arrow keys still step it.
                        Component.onCompleted: {
                          if (field.up.indicator) field.up.indicator.visible = false
                          if (field.down.indicator) field.down.indicator.visible = false
                        }
                        Connections {
                          target: levelField.field
                          function onActiveFocusChanged() { stageRepeater.anyFocused = levelField.field.activeFocus }
                        }
                      }
                    }
                  }
                  Row {
                    width: parent.width
                    spacing: Style.space(8)
                    Button { id: saveLevelsButton; text: "Save levels to mouse"; bordered: true; foreground: root.foreground; onClicked: root.saveDpi() }
                    Info {
                      visible: root.st.dpiLevel === 0
                      width: parent.width - saveLevelsButton.width - parent.spacing
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Custom: " + root.st.dpi + " DPI"
                    }
                  }
                }
              }

              Card {
                height: layoutsColumn.implicitHeight + inset * 2
                Column {
                  id: layoutsColumn
                  x: parent.inset
                  y: parent.inset
                  width: parent.width - parent.inset * 2
                  spacing: Style.space(10)

                  SectionHeader { text: "Saved layouts" }
                  MessageStrip { width: parent.width; visible: root.message !== "" && root.messageNearLayouts }
                  Info { text: "The mouse holds one layout at a time. Save the current setup here, then load any of them onto the mouse. Includes all buttons, DPI, polling and sleep." }
                  Info { visible: !!root.st.layoutsError; text: root.st.layoutsError || ""; color: root.urgent }
                  Repeater {
                    model: root.st.layouts || []
                    Rectangle {
                      required property string modelData
                      readonly property bool onMouse: root.st.loadedLayout === modelData
                      width: parent ? parent.width : 0
                      height: tileRow.implicitHeight + Style.space(10)
                      radius: root.capRadius
                      color: root.tinted(onMouse ? root.good : root.foreground, 0.06)
                      border.width: 1
                      border.color: onMouse ? root.good : root.tinted(root.foreground, 0.14)
                      Behavior on border.color { ColorAnimation { duration: root.motion(150) } }
                      Row {
                        id: tileRow
                        x: Style.space(10)
                        width: parent.width - Style.space(15)
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.space(6)
                        Text {
                          width: Math.max(0, parent.width - (onMouse ? onMouseBadge.width + Style.space(6) : 0) - loadButton.width - deleteButton.width - Style.space(12))
                          anchors.verticalCenter: parent.verticalCenter
                          textFormat: Text.PlainText
                          text: modelData
                          color: root.foreground
                          elide: Text.ElideRight
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                        }
                        Rectangle {
                          id: onMouseBadge
                          visible: onMouse
                          anchors.verticalCenter: parent.verticalCenter
                          width: badgeText.implicitWidth + Style.space(10)
                          height: badgeText.implicitHeight + Style.space(2)
                          radius: Style.space(3)
                          color: root.good
                          Text {
                            id: badgeText
                            anchors.centerIn: parent
                            textFormat: Text.PlainText
                            text: "ON MOUSE"
                            color: Commons.Color.background
                            font.family: root.fontFamily
                            font.pixelSize: Math.max(1, Style.font.caption - 1)
                            font.bold: true
                          }
                        }
                        Button {
                          id: loadButton
                          text: "Load"
                          bordered: true
                          foreground: root.foreground
                          onClicked: root.run(["apply-layout", modelData], "Loaded " + modelData + " onto the mouse")
                        }
                        Button {
                          id: deleteButton
                          text: root.confirmingDelete === modelData ? "Click again" : "Delete"
                          foreground: root.confirmingDelete === modelData ? root.urgent : root.foreground
                          onClicked: root.deleteClicked(modelData)
                        }
                      }
                    }
                  }
                  Row {
                    width: parent.width
                    spacing: Style.space(6)
                    TextField {
                      id: layoutName
                      width: parent.width - saveLayoutButton.width - Style.space(6)
                      placeholderText: "Name, like WoW or Desktop"
                      maximumLength: 40
                      foreground: root.foreground
                      font.family: root.fontFamily
                      onAccepted: root.saveLayout()
                      onTextChanged: if (text.trim() !== root.confirmingReplace) root.confirmingReplace = ""
                    }
                    Button {
                      id: saveLayoutButton
                      text: "Save current"
                      bordered: true
                      foreground: root.foreground
                      onClicked: root.saveLayout()
                    }
                  }
                  Column {
                    visible: root.confirmingReplace !== ""
                    width: parent.width
                    spacing: Style.space(6)
                    Info {
                      text: "A layout called " + root.confirmingReplace + " already exists."
                      color: root.urgent
                      font.pixelSize: Style.font.body
                    }
                    Row {
                      spacing: Style.space(6)
                      Button {
                        text: "Replace"
                        bordered: true
                        foreground: root.urgent
                        onClicked: root.saveLayoutNamed(root.confirmingReplace)
                      }
                      Button {
                        text: "Cancel"
                        foreground: root.foreground
                        onClicked: root.confirmingReplace = ""
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
