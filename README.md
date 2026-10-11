# DirectorLink · LG TV Notifications for Control4

Free, open-source Control4 driver that shows notifications as a pop-up on LG webOS TVs: a doorbell ring, a camera alert, a door or gate opened, the alarm. Part of [DirectorLink Drivers](https://directorlink.io/drivers).

It works on your local network only: the driver talks straight to the TV with LG's local API, the one LG's own phone app uses. No cloud, no LG account, no ThinQ, no extra hardware.

Free. No subscription, no license key, no account with us.

Made by [DirectorLink](https://directorlink.io), the open-source management layer for Control4 homes. Works on its own: DirectorLink is not required (Composer programming can show pop-ups too).

DirectorLink is an independent project, not affiliated with Control4 or Snap One. Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner.

**Download:** [releases](../../releases) · **Help and updates:** [directorlink.io/drivers/lg-tv](https://directorlink.io/drivers/lg-tv) (online with 1.0.0)

> **Beta.** The driver follows LG's local API as LG's Connect SDK and Home Assistant use it (with the changes LG's 2026 firmware needs), and passes its tests against a fake TV built from what those clients see. It has not run on a real LG TV yet. If you try it, please send the log: see [Trying the beta](#trying-the-beta).

## What you get

| | |
|---|---|
| A pop-up on the TV | The TV's own notification at the bottom of the screen, for about 5 seconds, over whatever is on |
| With DirectorLink 1.13 | DirectorLink's notifications: doorbell rings, camera alerts, doors and gates opened, the alarm, others; you choose which kinds each TV shows |
| Composer programming | **Show Notification** (Title, Message) for anything you want on the TV; *Notification Shown* and *Notification Failed*; `LAST_NOTIFICATION` |
| Quiet Hours | No pop-ups on this TV at night, or whenever you set |
| Several TVs | One driver per TV. Each has its own kinds and Quiet Hours; a notification shows on every TV that is on, at the same moment |

What it never does: turn the TV on, change its channel, input or volume, or keep anything off the TV's screen. When the TV is off, the pop-up is dropped.

## Requirements

- **Control4 OS 3.3.0 or newer** and Composer Pro.
- **An LG TV with webOS** (2014 and newer), including webOS 26 and its 2026 firmware.
- The TV and the controller on the same home network: the controller reaches the TV on port 3001 (port 3000 on TVs before 2018).
- **A fixed IP address for the TV** in the router.
- For DirectorLink's notifications: DirectorLink 1.13 or newer. Without it, the driver shows what Composer programming sends.

## Installation

1. Download `DirectorLink-LG-TV.c4z` from the [releases](../../releases).
2. In Composer Pro: **Driver → Add or Update Driver or Agent**.
3. Search **DirectorLink** and add **DirectorLink · LG TV Notifications** to the room of the TV. Add it once for each TV.

> **Keep the exact file name.** If your browser saves a second copy as `DirectorLink-LG-TV (1).c4z`, rename it before you upload. Composer knows a driver by its file name, so a renamed copy installs as a separate driver instead of updating the one you have.

## Setup

1. **Turn the TV on** (on, not in standby).
2. **Give the driver the TV's address.** In the driver's properties, pick the TV in **TVs Found** (the driver looks for LG TVs when it is added; run **Find TVs** to look again), or type its IP address in **TV IP Address**.
3. **Accept on the TV.** Within a few seconds the TV asks whether to allow a new device to connect. Accept it with the TV's remote. **Status** says *Paired*.
   The wording on the screen is the TV's own: it may not name DirectorLink. It appears right after you enter the address. If nobody accepts (the TV asks for about two minutes), **Status** says so: run **Pair Again** with the TV on.
4. Run **Test Notification**: the TV shows *Control4: Test: pop-ups from Control4 work on this TV.*
5. Choose what this TV shows: **Doorbell**, **Camera Alerts**, **Doors and Gates**, **Alarm**, **Other** (*On* or *Off*; Doorbell and Alarm are on), and **Quiet Hours** if you want them.

The TV remembers the driver: the request to accept comes once. **Pair Again** forgets the TV's answer and asks again.

### Status

| Status | Means |
|---|---|
| Paired | Connected and the TV is on: pop-ups show |
| Waiting for you to accept on the TV | The TV is asking: accept it with the remote |
| Off | The TV is off or in standby (it answered at this address before). Pop-ups are dropped until it is on |
| Not reachable | No TV has answered at this address yet: check the address, and that the TV is on and on the network |
| Not accepted on the TV: run Pair Again | The TV said no, or its question ran out. The driver does not ask again by itself |
| Not paired: turn the TV on, then run Pair Again | The TV asked and never answered |
| Enter the TV's IP address, or pick it in TVs Found | No address yet |

When **Quiet Hours** cannot be read, **Status** adds *Quiet Hours not understood: write it as 22:00-07:00*.

### Settings

| Property | |
|---|---|
| Status | What the driver is doing (above) |
| TV | The TV's model and webOS version, as the TV reports them |
| TV IP Address | The TV's IP address, for example `192.168.1.40` |
| TVs Found | The LG TVs that answered **Find TVs** (they answer while they are on). Picking one fills in TV IP Address |
| Doorbell · Camera Alerts · Doors and Gates · Alarm · Other | *On* or *Off*: which of DirectorLink's notifications this TV shows. Doorbell and Alarm are *On* by default |
| Quiet Hours | From-to in the controller's local time, for example `22:00-07:00` (also `22-7`): no pop-ups then. Empty: none |
| Log Level | *Info* writes one line for every pop-up. *Debug* and *Trace* show the connection and every message (never the TV's key) |

**Actions:** Find TVs · Test Notification · Pair Again · Print Diagnostics

**Print Diagnostics** shows the driver version, the TV and its webOS, the connection, the TV's power state, the kinds and Quiet Hours, the DirectorLink variables and the last 10 notifications with what happened to each. It never shows the TV's key.

### When a pop-up shows

- **Only when the TV is on.** Off, in standby, or with its screen off: the pop-up is dropped, with one line in the log. The driver never turns the TV on.
- **The TV has 3 seconds to answer.** When the driver is not connected at that moment (the TV was just turned on), it connects at once and the connection gets 3 seconds; then the pop-up gets its own 3 seconds. No answer: dropped, one log line.
- **One pop-up every 5 seconds at most,** on each TV. Others wait their turn; a doorbell or the alarm goes before the rest. One that waited more than 30 seconds (a minute for a doorbell or the alarm), or that does not fit (5 wait at most), is dropped.
- **Only the kinds switched on, and nothing during Quiet Hours.** Show Notification from programming is shown whatever the kinds, but not during Quiet Hours. Test Notification always shows.
- **The text as it was sent:** the title and the message, joined as `Title: Message`. Hebrew and other right-to-left languages show as given.
- **No picture.** On many LG TVs (webOS 4 and from webOS 6) a pop-up that carries a picture is not shown at all, so the driver never sends one.

### The connection

- The driver connects to the TV on port **3001** (encrypted; TVs with firmware from 2023 take nothing else), and on port **3000** when 3001 refuses (TVs before 2018). The one that worked is tried first next time.
- Every LG TV has the same certificate on port 3001, LG's own, with no address in it, so no client can check it (Home Assistant and every other client take it as it is). The driver connects only to the address in its properties.
- While connected it sends a ping every 15 seconds; a TV that stops answering is taken as gone. Then it tries again after 5 s, 30 s, 2 min, and then every 5 min.
- Many LG TVs keep their network on in standby (Quick Start+). The driver follows the TV's own power state, so a TV in standby counts as off.

## DirectorLink

DirectorLink 1.13 and newer sends its notifications to this driver as soon as both are in the project. Nothing needs to be set up: the driver follows the **DirectorLink notification agreement, version 1**, so DirectorLink recognizes it by what it says, not by its file name. Any driver may follow the agreement, for any kind of screen or speaker.

### The agreement, version 1

| What | The driver | DirectorLink |
|---|---|---|
| **Marker** | A string variable `DIRECTORLINK_NOTIFY`, value `1` (the agreement's version), set at every start | Knows the driver takes notifications |
| **Kinds** | A string variable `DIRECTORLINK_NOTIFY_KINDS`: the kinds the driver shows, comma-separated, from `doorbell`, `camera`, `door`, `alarm`, `other`; updated whenever its settings change (empty: none) | Sends only those kinds |
| **A notification** | Takes the command `DIRECTORLINK_NOTIFY` | Sends it with `C4:SendToDevice`, to every driver with the marker |

1. **The variables** are ordinary driver variables (`C4:AddVariable`), read by their names. A driver adds them **after its existing variables**, never reordering them: Director numbers variables in the order they are added, and projects use those numbers. This driver's are `LAST_NOTIFICATION`, then `DIRECTORLINK_NOTIFY`, then `DIRECTORLINK_NOTIFY_KINDS`. It writes the marker and the kinds at every start, also after an update.
2. **The command** `DIRECTORLINK_NOTIFY` comes by `C4:SendToDevice` from DirectorLink. Sent to the driver's own device it arrives in `ExecuteCommand`; sent to a proxy of the driver, in `ReceivedFromProxy`. A driver takes it from both. Its parameters, all strings:

   | Parameter | |
   |---|---|
   | `VERSION` | `1`. A later version only adds: read it as version 1 |
   | `ID` | Unique for each notification, the same for every target. The same ID again within 10 minutes is ignored |
   | `KIND` | `doorbell`, `camera`, `door`, `alarm` or `other` (another value is taken as `other`) |
   | `TITLE`, `MESSAGE` | The final text, in the home's language (it may be Hebrew, right to left), UTF-8. Shown exactly as given |
   | `PICTURE` | Optional. An `http://` address on the controller that gives a JPEG for 60 seconds. This driver does not use it (above) |

3. **Rules for the driver:**
   - **Never block.** `ExecuteCommand` returns at once; the pop-up follows.
   - **Show only the kinds switched on, and nothing during Quiet Hours** (or the driver's own equivalent).
   - **At most one pop-up every 5 seconds** on each screen. A doorbell or the alarm goes before the others waiting.
   - **Set `LAST_NOTIFICATION`, then fire `Notification Shown`**, or `Notification Failed` when the screen did not take it (off, not reachable, no answer within 3 seconds). A notification left out on purpose (a kind switched off, Quiet Hours, a repeated ID) fires neither.
   - **Work the same without DirectorLink.** The marker and the command are all the agreement adds.

### Sending a notification (DirectorLink's side)

```lua
-- To each device whose driver says DIRECTORLINK_NOTIFY = "1" and lists the kind in
-- DIRECTORLINK_NOTIFY_KINDS; the same ID to all of them.
for _, deviceId in ipairs(notifyTargets) do
    C4:SendToDevice(deviceId, "DIRECTORLINK_NOTIFY", {
        VERSION = "1",
        ID = "ring-158-1760206443",
        KIND = "doorbell",
        TITLE = "Front door",
        MESSAGE = "Someone is at the door",
    })
end
```

### Taking a notification (another driver's side)

```lua
function OnDriverInit()
    -- after the driver's existing variables
    C4:AddVariable("DIRECTORLINK_NOTIFY", "1", "STRING", true, false)
    C4:AddVariable("DIRECTORLINK_NOTIFY_KINDS", "doorbell,alarm", "STRING", true, false)
end

local function Notify(params)
    -- params.VERSION, params.ID, params.KIND, params.TITLE, params.MESSAGE, params.PICTURE
    -- Check the ID, the kind and quiet hours, queue it, return at once.
end

function ExecuteCommand(command, params)
    if command == "DIRECTORLINK_NOTIFY" then Notify(params) end
end

function ReceivedFromProxy(binding, command, params)
    if command == "DIRECTORLINK_NOTIFY" then Notify(params) end
end
```

Without DirectorLink the driver works the same: Composer programming shows pop-ups with **Show Notification**.

## Programming

- **Command:** **Show Notification** (`SHOW_NOTIFICATION`), with **Title** and **Message**. Shown whatever the kinds, not during Quiet Hours. For example: *When* the garage door has been open for 10 minutes → Show Notification *Garage*, *The garage door is still open*.
- **Events:** **Notification Shown** · **Notification Failed** (the TV was off, not reachable, did not answer within 3 seconds, or is not paired).
- **Variable:** `LAST_NOTIFICATION` (STRING): the text of the last pop-up shown or failed, as `Title: Message`. Set before the event fires.
- **Several TVs:** add Show Notification for each TV in the same *When*; they run one after the other at once, so the TVs show it together.
- **A fallback:** *When* **Notification Failed** → send the text as a push notification instead (`LAST_NOTIFICATION`).

## Updating

1. Download the new `DirectorLink-LG-TV.c4z` from the [releases](../../releases).
2. Check that the name is exactly `DirectorLink-LG-TV.c4z`, not `... (1).c4z`.
3. **Driver → Add or Update Driver or Agent**. The driver keeps its settings, the TV's key (no new request on the TV) and the programming.

## Troubleshooting

| What you see | What to do |
|---|---|
| Not reachable | Check **TV IP Address**, that the TV is on, and that the controller and the TV are on the same network. **Find TVs** lists the TVs that answer |
| The TV did not ask to accept | Turn the TV on (not standby), run **Pair Again**, and look at the TV within a few seconds |
| Not accepted on the TV | Run **Pair Again** and accept on the TV within two minutes |
| *Paired*, but no pop-ups from DirectorLink | Check the kinds (**Doorbell**, **Camera Alerts** ...) and **Quiet Hours**. **Print Diagnostics** lists the last 10 notifications and why each was skipped or not shown |
| Off, but the TV is on | The TV reports standby or its screen off. Run **Print Diagnostics** (the power state) and send the log |
| The TV asks to accept again after a while | The TV forgot the driver (a reset, or the device removed in the TV's settings): accept it again |
| No TV in TVs Found | The TV answers only while it is on. Type its IP address instead |

For details run **Print Diagnostics**, with **Log Level** set to *Debug*. The output appears in the Lua tab and in the driver log.

## Trying the beta

1. Give the TV a fixed IP address and turn it on.
2. Add **DirectorLink · LG TV Notifications** to the TV's room, set **Log Level** to *Trace*, and pick the TV in **TVs Found** (or type its address).
3. Accept on the TV. Note the words the TV shows (a photo helps).
4. Run **Test Notification**, then **Show Notification** from programming with a Hebrew title and message.
5. Put the TV in standby and run **Test Notification** again: the log says it was not shown. Turn the TV on: **Status** says *Paired* within a few minutes, or at once on the next pop-up.
6. Run **Print Diagnostics** and send it with the Lua tab output (or `driver_log.log`) in an [issue](../../issues).

Every line of the driver carries `LG TV '<name>':` after its time and level. At *Trace* the log shows every message to and from the TV. It never shows the TV's key.

## Privacy

The driver talks only to the TV on your home network (and, for **Find TVs**, to the LG TVs that answer the search there). It sends nothing to DirectorLink, LG or anyone else, and collects no usage data.

The TV's key stays in your Control4 project and is never written to a log, a property or Print Diagnostics. The driver asks the TV only for permission to show pop-ups and to read whether it is on: the key cannot change channels, inputs, volume, settings or power.

## Support

Community-supported, best effort. Report problems in [Issues](../../issues).

Please include the TV model and webOS version (the **TV** property), your Control4 OS version, the **Print Diagnostics** output (it shows the driver version) and the log with **Log Level** set to *Debug*. Remove addresses and names you don't want to share. Reports that a TV works are welcome too. Security problems: see [SECURITY.md](SECURITY.md).

## Building from source

```
pip install lupa pillow
python tools/make_icons.py       # after changing icons
cd tests && python test_static.py && python test_xml.py && python test_protocol.py && python test_driver.py && cd ..
python tools/build.py            # -> dist/DirectorLink-LG-TV.c4z, dist/SHA256SUMS.txt
```

`test_static.py` needs `luac` 5.1 (`apt install lua5.1`).

`tests/live_test.py` runs the same driver code against a real LG TV from a computer on the TV's network (not part of CI): `python tests/live_test.py find` runs the driver's own Find TVs; `python tests/live_test.py <TV IP>` pairs (accept on the TV), prints the diagnostics and shows two pop-ups, one in Hebrew. That computer pairs as a device of its own; its key is kept in `~/.directorlink/lg-tv-live.json`, never in the repository.

| Path | Contents |
|---|---|
| `src/core.lua` | Logging with secret redaction (the TV's key), timers, variables and events, saved settings, utilities |
| `src/json.lua` | JSON (UTF-8 text through as it is) |
| `src/websocket.lua` | WebSocket client (RFC 6455) over a Control4 network connection, TLS or plain |
| `src/webos.lua` | The TV: connecting (3001, then 3000), pairing, the power state, the pop-up, reconnecting |
| `src/ssdp.lua` | Find TVs (SSDP) |
| `src/notify.lua` | Notifications: IDs, kinds, Quiet Hours, one every 5 seconds, a doorbell or the alarm first |
| `src/driver.lua`, `src/driver.xml`, `src/www/` | The driver, its definition, documentation and icons |
| `tools/` | `build.py` (package), `make_icons.py` (icons) |
| `tests/` | `harness.py` (Lua 5.1 with a fake Director, its network and a virtual clock), `fake_tv.py` (an LG TV), the tests, and `live_test.py` (the driver against a real TV) |
| `VERSION`, `CHANGELOG.md`, `docs/releases/` | The version, the change log, and the notes for each release |

`build.py` puts the Lua parts together and stamps the version from `VERSION` and the build date into the packaged copy. The driver version Composer compares is major·1000000 + minor·10000 + patch·100 + build, where build is the beta number, or 99 for the release: `1.0.0-beta.1` → `1000001`, `1.0.0` → `1000099`.

The tests run the real driver code in Lua 5.1 against a fake Director and `fake_tv.py`, an LG TV that answers the way the open-source clients record it (the WebSocket handshake, hello, getSystemInfo, the pairing prompt and its 403, the client key, the power state with its changes, the pop-up, pings), with ports that refuse, a TV that goes away without a word, one that stops answering pop-ups, and one in standby. They cover the agreement's variables and their order, both ways the command comes in, VERSION, KIND and ID repeats, the kinds, Quiet Hours (across midnight), one pop-up every 5 seconds with a doorbell or the alarm first, the queue's limits, the TV off, in standby and on its way off, the 3-second answer, reconnecting (5 s, 30 s, 2 min, then every 5 min), the ping, port 3000 after 3001, pairing (accepted, refused, timed out, never answered, a key the TV forgot, Pair Again), a new address, Find TVs, a restart, and that the key is in no log line (at *Trace* too), property, variable or Print Diagnostics.

**How it works**

- **The TV:** a WebSocket to `wss://<TV>:3001/` (or `ws://<TV>:3000/`) with LG's SSAP messages: `hello`, `ssap://system/getSystemInfo`, `register` (an unsigned manifest asking for `WRITE_NOTIFICATION_TOAST` and `READ_POWER_STATE`; the TV answers `registered` with a client key, or asks first), a subscription to `ssap://com.webos.service.tvpower/power/getPowerState`, and `ssap://system.notifications/createToast` with `{"message": "<title>: <message>"}`.
- **Why unsigned:** the signed "LG Remote App" manifest the open-source clients copied for years is refused by LG's 2026 firmware (`403 Pairing rejected: blacklisted certificate detected`). LG's own Connect SDK pairs without a signature, and pop-ups and the power state need none.
- **Find TVs:** SSDP `M-SEARCH` for `urn:lge-com:service:webos-second-screen:1` to `239.255.255.250:1900`, the TV's name from the description at its `LOCATION`.
- **Saved:** the TV's key and the pairing state, as `json:` text in the driver's persisted data, which survives driver updates.

**Rules for new versions**

- `VERSION` is the only place the version is set.
- **Never rename** the `.c4z` file, and **never change or remove** the order of variables, event ids, command and property names. Projects (and DirectorLink) depend on them. Adding new ones at the end is fine.
- Update `VERSION`, `CHANGELOG.md`, the change log in `src/www/documentation.html`, and `docs/releases/v<version>.md`. Then tag `v<version>`: GitHub Actions tests, builds and publishes the release with `SHA256SUMS.txt` (a pre-release for `-beta` versions). Releases are immutable.

## License and trademarks

[Apache License 2.0](LICENSE). See also [NOTICE](NOTICE).

How the TV's local API works was learned from LG's [Connect SDK](https://github.com/ConnectSDK/Connect-SDK-Android-Core), Home Assistant's [aiowebostv](https://github.com/home-assistant-libs/aiowebostv), [LGWebOSRemote](https://github.com/klattimer/LGWebOSRemote), [lgtv2](https://github.com/hobbyquaker/lgtv2) and openHAB's LG webOS binding. No code was copied from them. The WebSocket client follows the design of DirectorLink's own. Control4 interfaces follow the public [Snap One DriverWorks documentation](https://snap-one.github.io/docs-driverworks-fundamentals/). DirectorLink's own source code: [github.directorlink.io](https://github.directorlink.io).

Installing third-party drivers or changing a Control4 project can affect compatibility, support, warranty or recovery. Keep a backup of your project.

Apache License 2.0. Copyright 2026 DirectorLink. DirectorLink is an independent project, not affiliated with Control4 or Snap One. Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner. Product names are trademarks of their owners.
