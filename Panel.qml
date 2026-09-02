import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root

  moduleName: "io.github.goooooooooody.omarchy-pebble-index"
  ipcTarget: "io.github.goooooooooody.omarchy-pebble-index"

  property string focusSection: "captures"
  property int selectedIndex: 0
  property bool cursorActive: false
  property bool refreshing: false
  property bool settingUp: false
  property bool stopping: false
  property string lastAction: ""
  property string setupError: ""
  property string copied: ""

  property var state: ({
    online: false,
    pending: 0,
    failed: 0,
    total: 0,
    provisioned: false,
    events: []
  })

  property var webhook: ({
    ok: false,
    provisioned: false,
    url: "",
    token: "",
    authorization: "",
    address: "",
    port: 8787,
    error: ""
  })

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color barForeground: bar ? bar.barForeground : foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string pluginHome: (Quickshell.env("HOME") || "") + "/.config/omarchy/plugins/io.github.goooooooooody.omarchy-pebble-index"
  readonly property string stateCommand: pluginHome + "/state.sh"
  readonly property string cli: pluginHome + "/bin/pebble-index"

  readonly property bool online: state && state.online === true
  readonly property bool provisioned: (state && state.provisioned === true) || (webhook && webhook.provisioned === true)
  readonly property bool hasWebhook: !!(webhook && webhook.provisioned && webhook.url)
  readonly property bool installBusy: settingUp || stopping
  readonly property var events: state && Array.isArray(state.events) ? state.events : []
  readonly property int failed: Number(state.failed || 0)
  readonly property int pending: Number(state.pending || 0)
  readonly property int total: Number(state.total || 0)
  readonly property bool hasCaptures: events.length > 0
  readonly property color iconColor: online ? (failed > 0 ? urgent : foreground) : dim
  readonly property color barIconColor: online ? (failed > 0 ? urgent : barForeground) : Qt.darker(barForeground, 1.55)
  readonly property string heroMeta: {
    if (settingUp) return "Starting"
    if (stopping) return "Stopping"
    if (!provisioned) return "Needs setup"
    if (!online) return "Not running"
    if (failed > 0) return "Dispatch failed"
    if (pending > 0) return "Working"
    if (total === 0) return "Waiting"
    return "Up to date"
  }
  readonly property string heroDetail: {
    if (settingUp) return "…"
    if (stopping) return "…"
    if (!provisioned) return "Start"
    if (!online) return "Offline"
    if (failed > 0) return failed + " failed"
    if (pending > 0) return pending + " pending"
    if (total === 0) return "Empty"
    return total + " held"
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function parseState(raw) {
    try {
      var parsed = JSON.parse(String(raw || ""))
      if (parsed && typeof parsed === "object") {
        state = parsed
        ensureCursor()
      }
    } catch (e) {
      console.warn("io.github.goooooooooody.omarchy-pebble-index: invalid state", e)
    }
  }

  function refresh() {
    if (stateProcess.running) return
    refreshing = true
    stateProcess.running = true
  }

  function runCli(args) {
    if (actionProcess.running) return
    lastAction = args.join(" ")
    actionProcess.command = [root.cli].concat(args)
    actionProcess.running = true
  }

  function startReceiver() {
    if (installProcess.running) return
    settingUp = true
    setupError = ""
    lastAction = "setup"
    installProcess.command = [root.cli, "setup", "--json"]
    installProcess.running = true
  }

  function stopReceiver() {
    if (installProcess.running) return
    stopping = true
    setupError = ""
    lastAction = "uninstall"
    installProcess.command = [root.cli, "uninstall", "--json"]
    installProcess.running = true
  }

  function loadWebhook() {
    if (webhookProcess.running) return
    webhookProcess.running = true
  }

  function copyWebhook(field) {
    if (copyProcess.running) return
    lastAction = "copy " + field
    copyProcess.command = [root.cli, "webhook", "--copy", field]
    copyProcess.running = true
  }

  function parseWebhook(raw) {
    try {
      var parsed = JSON.parse(String(raw || ""))
      if (parsed && typeof parsed === "object") webhook = parsed
    } catch (e) {
    }
  }

  function parseInstall(raw, command) {
    try {
      var parsed = JSON.parse(String(raw || ""))
      if (!parsed || typeof parsed !== "object") {
        setupError = "Could not read setup output"
        return
      }
      if (command === "setup") {
        webhook = parsed
        setupError = parsed.ok === false ? (parsed.error || "Could not start the receiver") : (parsed.error || "")
        return
      }
      setupError = parsed.ok === false ? (parsed.error || "Could not stop the receiver") : ""
    } catch (e) {
      setupError = "Could not read setup output"
    }
  }

  function ensureCursor() {
    if (selectedIndex >= events.length) selectedIndex = Math.max(0, events.length - 1)
    if (events.length === 0) focusSection = "header"
    else if (focusSection === "header" && cursorActive) focusSection = "header"
  }

  function selectedEvent() {
    if (events.length === 0) return null
    return events[Math.max(0, Math.min(selectedIndex, events.length - 1))]
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
  }

  function setCaptureCursor(index) {
    cursorActive = true
    focusSection = "captures"
    selectedIndex = index
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && hasCaptures) focusSection = "captures"
      return
    }
    if (dy < 0 && selectedIndex === 0) {
      focusSection = "header"
      return
    }
    selectedIndex = Math.max(0, Math.min(events.length - 1, selectedIndex + dy))
  }

  function activateCursor() {
    if (focusSection === "header") {
      if (!online && !installBusy) startReceiver()
      else refresh()
      return
    }
    var item = selectedEvent()
    if (item && item.id) runCli(["open", item.id])
  }

  function rerunSelected() {
    var item = selectedEvent()
    if (item && item.id && item.dispatchStatus !== "test") runCli(["replay", item.id, "--force"])
  }

  function actionGlyph(action, status) {
    if (status === "test") return "󰙨"
    if (action === "reminder") return "󰢌"
    if (action === "calendar") return "󰃭"
    if (action === "herdr") return "󰚩"
    if (action === "agent") return "󰀎"
    if (action === "note") return "󰎞"
    return "󰻃"
  }

  function prettyAction(action, status) {
    if (status === "test") return "Test"
    if (status === "failed") return "Failed"
    if (status === "pending") return "Pending"
    if (action === "reminder") return "Reminder"
    if (action === "calendar") return "Calendar"
    if (action === "herdr") return "Herdr"
    if (action === "agent") return "Agent"
    if (action === "note") return "Note"
    if (action) return action.charAt(0).toUpperCase() + action.slice(1)
    return "Capture"
  }

  function relativeTime(iso) {
    var ms = Date.parse(String(iso || ""))
    if (isNaN(ms)) return ""
    var seconds = Math.max(0, Math.floor((Date.now() - ms) / 1000))
    if (seconds < 45) return "just now"
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago"
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago"
    return Math.floor(seconds / 86400) + "d ago"
  }

  function eventSubtitle(item) {
    var parts = [prettyAction(item.action, item.dispatchStatus)]
    var when = relativeTime(item.recordedAt || item.createdAt)
    if (when !== "") parts.push(when)
    return parts.join(" · ")
  }

  onOpenedChanged: if (opened) {
    cursorActive = false
    if (panelFlick) panelFlick.contentY = 0
    refresh()
    loadWebhook()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Process {
    id: stateProcess
    command: [root.stateCommand]
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parseState(text)
    }
    onExited: function(exitCode) {
      root.refreshing = false
      if (exitCode !== 0) console.warn("io.github.goooooooooody.omarchy-pebble-index: state exited", exitCode)
    }
  }

  Process {
    id: actionProcess
    running: false
    onExited: function(exitCode) {
      root.refresh()
      if (exitCode !== 0) console.warn("io.github.goooooooooody.omarchy-pebble-index: action failed", root.lastAction, exitCode)
    }
  }

  Process {
    id: installProcess
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parseInstall(text, root.lastAction)
    }
    onExited: function(exitCode) {
      var command = root.lastAction
      root.settingUp = false
      root.stopping = false
      root.refresh()
      root.loadWebhook()
      if (exitCode !== 0 && root.setupError === "")
        root.setupError = command === "uninstall" ? "Could not stop the receiver" : "Could not start the receiver"
    }
  }

  Process {
    id: webhookProcess
    command: [root.cli, "webhook", "--json"]
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.parseWebhook(text)
    }
  }

  Process {
    id: copyProcess
    running: false
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.copied = root.lastAction.indexOf("token") >= 0 ? "token" : (root.lastAction.indexOf("authorization") >= 0 ? "authorization" : "url")
        copyClear.restart()
      } else {
        root.setupError = "Could not copy to the clipboard"
      }
    }
  }

  Timer {
    interval: 4000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: copyClear
    interval: 2000
    onTriggered: root.copied = ""
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰻃"
    active: root.failed > 0 || root.pending > 0
    activeColor: root.failed > 0 ? root.urgent : root.barForeground
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r") root.refresh()
        else if (text === "R") root.rerunSelected()
        else if (text === "s") root.startReceiver()
        else if (text === "x" && root.provisioned) root.stopReceiver()
        else if (text === "o" || text === "O") root.activateCursor()
      }

      Flickable {
        id: panelFlick
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
          width: panelFlick.width
          spacing: Style.space(12)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            readonly property bool ringVisible: root.cursorActive && root.focusSection === "header"
            function focusHero() { root.setHeaderCursor() }

            PanelHero {
              id: hero
              width: parent.width
              title: "Pebble Index"
              meta: root.heroMeta
              detail: root.heroDetail
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconOpacity: root.online ? 1.0 : 0.5
              iconComponent: Component {
                Text {
                  textFormat: Text.PlainText
                  text: "󰻃"
                  color: root.iconColor
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                }
              }
              trailingControl: Component {
                PanelActionButton {
                  id: refreshButton
                  iconText: "󰑐"
                  tooltipText: "Refresh inbox"
                  foreground: hero.foreground
                  fontFamily: hero.fontFamily
                  hasCursor: header.ringVisible
                  onHovered: function(on) { if (on) header.focusHero() }
                  onClicked: root.refresh()
                }
              }
            }
          }

          CursorSurface {
            visible: !root.online
            width: parent.width
            implicitHeight: offlineText.implicitHeight + Style.spacing.rowPaddingX
            foreground: root.foreground

            Text {
              id: offlineText
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(12)
              textFormat: Text.PlainText
              text: root.provisioned
                ? "The webhook receiver is not running."
                : "Start the receiver to get a Tailscale URL and token for CoreApp."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }
          }

          CursorSurface {
            visible: root.setupError !== ""
            width: parent.width
            implicitHeight: setupErrorText.implicitHeight + Style.spacing.rowPaddingX
            foreground: root.urgent

            Text {
              id: setupErrorText
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(12)
              textFormat: Text.PlainText
              text: root.setupError
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
            }
          }

          CursorSurface {
            visible: !root.online
            width: parent.width
            implicitHeight: startLabel.implicitHeight + Style.spacing.rowPaddingX
            foreground: root.foreground

            MouseArea {
              anchors.fill: parent
              acceptedButtons: Qt.LeftButton
              hoverEnabled: true
              cursorShape: root.installBusy ? Qt.ArrowCursor : Qt.PointingHandCursor
              enabled: !root.installBusy
              onClicked: root.startReceiver()
            }

            Text {
              id: startLabel
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(12)
              textFormat: Text.PlainText
              text: root.settingUp ? "Starting receiver…" : "Start receiver"
              color: root.installBusy ? root.dim : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
          }

          PanelSeparator {
            visible: root.hasWebhook || root.online
            foreground: root.foreground
          }

          Column {
            visible: root.hasWebhook
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "COREAPP"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              width: parent.width
              leftPadding: Style.space(12)
              rightPadding: Style.space(12)
              textFormat: Text.PlainText
              text: "Paste into Index → Webhook on your phone."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            WebhookRow {
              width: parent.width
              label: "URL"
              value: String(root.webhook.url || "")
              field: "url"
            }

            WebhookRow {
              width: parent.width
              label: "Authorization"
              value: String(root.webhook.authorization || "")
              field: "authorization"
            }

            Text {
              visible: root.copied !== ""
              width: parent.width
              leftPadding: Style.space(12)
              rightPadding: Style.space(12)
              textFormat: Text.PlainText
              text: root.copied === "url" ? "Copied URL" : "Copied bearer token"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          PanelSeparator {
            visible: root.online
            foreground: root.foreground
          }

          Column {
            visible: root.online
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: root.hasCaptures ? "CAPTURES" : "CAPTURES"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            CursorSurface {
              visible: !root.hasCaptures
              width: parent.width
              implicitHeight: emptyText.implicitHeight + Style.spacing.rowPaddingX
              foreground: root.foreground

              Text {
                id: emptyText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(12)
                textFormat: Text.PlainText
                text: "No captures yet. Speak a note into the ring."
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: root.events
                EventRow {
                  required property var modelData
                  required property int index
                  width: parent.width
                  item: modelData
                  rowIndex: index
                }
              }
            }
          }

          PanelSeparator {
            visible: root.provisioned
            foreground: root.foreground
          }

          Column {
            visible: root.provisioned
            width: parent.width
            spacing: Style.space(6)

            CursorSurface {
              width: parent.width
              implicitHeight: stopLabel.implicitHeight + Style.spacing.rowPaddingX
              foreground: root.urgent

              MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                cursorShape: root.installBusy ? Qt.ArrowCursor : Qt.PointingHandCursor
                enabled: !root.installBusy
                onClicked: root.stopReceiver()
              }

              Text {
                id: stopLabel
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(12)
                textFormat: Text.PlainText
                text: root.stopping ? "Stopping receiver…" : "Stop receiver"
                color: root.installBusy ? root.dim : root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
            }

            Text {
              width: parent.width
              leftPadding: Style.space(12)
              rightPadding: Style.space(12)
              textFormat: Text.PlainText
              text: "Leaves notes and config. Menu plugin removal does not stop this."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }
        }
      }
    }
  }

  component WebhookRow: CursorSurface {
    id: hookRow
    property string label: ""
    property string value: ""
    property string field: "url"

    foreground: root.foreground
    implicitHeight: Math.max(hookText.implicitHeight, hookCopy.implicitHeight) + Style.spacing.rowPaddingX + Style.space(4)

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(12)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(8)

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: hookRow.label
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          id: hookText
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: hookRow.value
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WrapAnywhere
        }
      }

      PanelActionButton {
        id: hookCopy
        iconText: "󰆏"
        tooltipText: root.copied === hookRow.field ? "Copied" : "Copy"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: hookRow.value !== ""
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.copyWebhook(hookRow.field)
      }
    }
  }

  component EventRow: CursorSurface {
    id: eventRow
    property var item: ({})
    property int rowIndex: 0

    readonly property string status: String(item.dispatchStatus || "")
    readonly property string action: String(item.action || "")
    readonly property string preview: String(item.preview || item.transcription || "Untitled")

    hasCursor: root.cursorActive && root.focusSection === "captures" && root.selectedIndex === rowIndex
    foreground: root.foreground
    implicitHeight: Math.max(eventContent.implicitHeight, openButton.implicitHeight) + Style.spacing.rowPaddingX + Style.space(4)

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) root.setCaptureCursor(eventRow.rowIndex)
      onClicked: {
        root.setCaptureCursor(eventRow.rowIndex)
        if (eventRow.item.id) root.runCli(["open", String(eventRow.item.id)])
      }
    }

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: root.actionGlyph(eventRow.action, eventRow.status)
        color: eventRow.status === "failed" ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: eventContent
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: eventRow.preview
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: root.eventSubtitle(eventRow.item)
          color: eventRow.status === "failed" ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        id: openButton
        iconText: "󰈮"
        tooltipText: "Open"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !!eventRow.item.id
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.runCli(["open", String(eventRow.item.id || "")])
      }

      PanelActionButton {
        iconText: "󰑐"
        tooltipText: "Re-run (creates another)"
        foreground: root.foreground
        hoverColor: root.urgent
        fontFamily: root.fontFamily
        visible: eventRow.status !== "test"
        enabled: eventRow.status !== "test" && !!eventRow.item.id
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.runCli(["replay", String(eventRow.item.id || ""), "--force"])
      }
    }
  }
}
