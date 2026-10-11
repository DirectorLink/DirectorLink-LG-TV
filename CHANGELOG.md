# Changelog

All notable changes to DirectorLink · LG TV Notifications for Control4. Versions follow the `VERSION` file; the driver version Composer compares is in brackets.

## 1.0.0-beta.1 (1000001) - 2026-10-11

First release, a **beta**: built on LG's local webOS API as LG's Connect SDK and Home Assistant's aiowebostv use it (with the unsigned pairing LG's 2026 firmware needs), tested against a fake TV and a fake Director, not yet confirmed on a real LG TV.

- One driver, `DirectorLink-LG-TV.c4z` (*DirectorLink · LG TV Notifications* in Composer), one per TV: a pop-up on an LG webOS TV, on the home network only. No cloud, no LG account, no ThinQ.
- The TV: a WebSocket on port 3001 (TLS, the TV's own certificate taken as it is), port 3000 when 3001 refuses (TVs before 2018); hello, getSystemInfo and register, as aiowebostv does it; the pop-up with `ssap://system.notifications/createToast`.
- Pairing: the TV asks once, accepted with its remote; the client key is saved (plain persistence, `json:`, which survives driver updates) and never logged, shown or printed. The driver asks only for pop-ups and the power state. After a refusal, or a question nobody answered, it does not ask again until **Pair Again**.
- Only when the TV is on: the TV's power state is followed (standby, screen off, on its way off: no pop-up). Off, or no answer within 3 seconds: dropped with one log line, *Notification Failed*. The driver never turns the TV on. No picture in the pop-up (many webOS versions show nothing when it carries one).
- Reconnects after 5 s, 30 s, 2 min, then every 5 min; a ping every 15 s; a notification while not connected connects at once.
- DirectorLink notification agreement, version 1: `DIRECTORLINK_NOTIFY` = `1` and `DIRECTORLINK_NOTIFY_KINDS` (after `LAST_NOTIFICATION`, written at every start, the kinds at every change); the command `DIRECTORLINK_NOTIFY` by `ExecuteCommand` or `ReceivedFromProxy` (VERSION, ID, KIND, TITLE, MESSAGE, PICTURE); the same ID within 10 minutes is ignored.
- Properties: Status, TV, TV IP Address, TVs Found (an SSDP search), Doorbell, Camera Alerts, Doors and Gates, Alarm, Other (Doorbell and Alarm on), Quiet Hours, Log Level. Actions: Find TVs, Test Notification, Pair Again, Print Diagnostics.
- At most one pop-up every 5 seconds per TV, a doorbell or the alarm first; nothing during Quiet Hours.
- Programming: **Show Notification** (Title, Message); *Notification Shown*, *Notification Failed*; `LAST_NOTIFICATION`.

---

DirectorLink is an independent project, not affiliated with Control4 or Snap One. Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner.
