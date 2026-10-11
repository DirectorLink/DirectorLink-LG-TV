"""The driver against a webOS TV (offline).   python tests/test_driver.py

The real driver code in Lua 5.1, a fake Director (harness.py) and a fake LG TV
(fake_tv.py): the WebSocket handshake, port 3001 then 3000, the pairing prompt
(accepted, declined, unanswered), the client key saved and used again (never in a log,
a property, a variable or Print Diagnostics), pop-ups (Hebrew as given), the TV off or in
standby, no answer within 3 s, reconnecting (5 s, 30 s, 2 min, then every 5 min),
the ping, the DirectorLink notification agreement (variables, both ways in, VERSION,
KIND, ID repeats), the kinds, Quiet Hours, one pop-up every 5 s with a doorbell or the
alarm first, Find TVs, and Pair Again.
"""
import json

from fake_tv import POWER_URI, TOAST_URI, FakeSSDP, FakeTV
from harness import Driver, check, finish, utf8

IP = "192.168.50.40"
KEY_PREFIX = "0123456789abcdef"
HE_TITLE, HE_MESSAGE = "דלת הכניסה", "מישהו מצלצל בדלת"


def paired(props=None, persist=None, tv=None, accept=True, **kw):
    """A driver with the TV's address set and (accept) paired: (driver, tv)."""
    p = {"TV IP Address": IP}
    p.update(props or {})
    d = Driver(props=p, persist=persist, **kw)
    tv = tv or FakeTV(IP)
    tv.attach(d)
    d.start()
    d.pump(3000)
    if accept and tv.prompting():
        tv.accept()
    d.pump(2000)
    return d, tv


def notify(d, kind="doorbell", nid=None, title="Front Door", message="Someone is at the door", via="ExecuteCommand", **extra):
    params = {"VERSION": "1", "KIND": kind, "TITLE": title, "MESSAGE": message}
    if nid is not None:
        params["ID"] = nid
    params.update(extra)
    if via == "ExecuteCommand":
        return d.command("DIRECTORLINK_NOTIFY", params)
    return d.from_proxy(5001, "DIRECTORLINK_NOTIFY", params)


def connects(tv):
    return [c for c in tv.connects]


def secret_free(d, key, label):
    logs = "\n".join(d.logs())
    props = " ".join(str(d.prop(n)) for n in ("Status", "TV", "TV IP Address", "TVs Found", "Quiet Hours"))
    vars_ = " ".join(str(d.var(n)) for n in d.var_order())
    check(key not in logs and key[-12:] not in logs, f"{label}: the client key is in no log line")
    check(key not in props and key not in vars_, f"{label}: the client key is in no property and no variable")


# ============================================================ variables (the agreement)
d = Driver()
d.start()
check(d.var_order() == ["LAST_NOTIFICATION", "DIRECTORLINK_NOTIFY", "DIRECTORLINK_NOTIFY_KINDS"],
      f"variables in their fixed order: LAST_NOTIFICATION, then the agreement's ({d.var_order()})")
check(d.var("DIRECTORLINK_NOTIFY") == "1", "DIRECTORLINK_NOTIFY = 1")
check(d.var("DIRECTORLINK_NOTIFY_KINDS") == "doorbell,alarm", f"DIRECTORLINK_NOTIFY_KINDS by default: doorbell,alarm ({d.var('DIRECTORLINK_NOTIFY_KINDS')})")
check(d.prop("Status").startswith("Enter the TV's IP address"), f"no address: Status says to enter it ({d.prop('Status')})")
d.set_prop("Camera Alerts", "On")
check(d.var("DIRECTORLINK_NOTIFY_KINDS") == "doorbell,camera,alarm", "a kind switched on: DIRECTORLINK_NOTIFY_KINDS follows, in the agreement's order")
for name in ("Doorbell", "Camera Alerts", "Alarm"):
    d.set_prop(name, "Off")
check(d.var("DIRECTORLINK_NOTIFY_KINDS") == "", "every kind off: DIRECTORLINK_NOTIFY_KINDS is empty")
for name in ("Doorbell", "Camera Alerts", "Doors and Gates", "Alarm", "Other"):
    d.set_prop(name, "On")
check(d.var("DIRECTORLINK_NOTIFY_KINDS") == "doorbell,camera,door,alarm,other", "every kind on: all five")

# Written at every start (also after an update): a restart writes them again, the saved LAST_NOTIFICATION comes back
d, tv = paired()
d.command("SHOW_NOTIFICATION", {"Title": "Hello", "Message": "World"})
d.pump(1000)
persist = d.persisted()
d2 = Driver(props={"TV IP Address": IP, "Alarm": "Off"}, persist=persist)
FakeTV(IP).attach(d2)
d2.start()
logged = [n for n in d2.g.VAR_LOG.values()]
check("DIRECTORLINK_NOTIFY" in logged and "DIRECTORLINK_NOTIFY_KINDS" in logged, "a restart writes DIRECTORLINK_NOTIFY and DIRECTORLINK_NOTIFY_KINDS again")
check(d2.var("DIRECTORLINK_NOTIFY_KINDS") == "doorbell", "the kinds are read from the properties at the start")
check(d2.var("LAST_NOTIFICATION") == "Hello: World", f"LAST_NOTIFICATION comes back after a restart ({d2.var('LAST_NOTIFICATION')})")
check(d2.var_order() == ["LAST_NOTIFICATION", "DIRECTORLINK_NOTIFY", "DIRECTORLINK_NOTIFY_KINDS"], "the same order after a restart")

# ============================================================ the connection and the handshake
d = Driver(props={"TV IP Address": IP})
tv = FakeTV(IP).attach(d)
d.start()
d.pump(3000)
opts = [r for r in d.net_log("options")]
check(tv.connects and tv.connects[0][0] == 3001, f"connects to port 3001 first ({tv.connects})")
o = dict(opts[0][4]) if opts else {}
check(opts and opts[0][3] == "SSL" and o.get("VERIFY_MODE") == "none" and o.get("MONITOR_CONNECTION") is False and o.get("AUTO_CONNECT") is False,
      f"port 3001 is TLS, the TV's self-signed certificate taken, Director's monitoring off ({opts[:1]})")
creates = d.net_log("create")
check(creates and creates[0][2] == IP and creates[0][1] == 6001, f"the connection goes to the TV's address, binding 6001 ({creates})")
c = tv.conn()
head = c.request_head if c else ""
check(head.startswith("GET / HTTP/1.1\r\n") and f"Host: {IP}:3001" in head and "Upgrade: websocket" in head and "Sec-WebSocket-Version: 13" in head
      and "Sec-WebSocket-Key: " in head and "User-Agent: DirectorLink-LG-TV/" in head, "the WebSocket upgrade request")
order = [(m.get("type"), m.get("uri")) for m in tv.messages]
check(order[:3] == [("hello", None), ("request", "ssap://system/getSystemInfo"), ("register", None)],
      f"the opening, as aiowebostv does it: hello, getSystemInfo, then register ({order[:3]})")
check(tv.messages[0].get("payload") == {} and tv.messages[1].get("payload") == {}, "hello and getSystemInfo carry an empty payload {} (webOS 25 needs it)")
regs = tv.registers()
check(len(regs) == 1 and "client-key" not in regs[0]["payload"] and regs[0]["payload"].get("pairingType") == "PROMPT"
      and regs[0]["payload"].get("forcePairing") is False, "first time: register without a client key, asking for the prompt")
manifest = regs[0]["payload"].get("manifest", {}) if regs else {}
check("signed" not in manifest and "signatures" not in manifest and manifest.get("manifestVersion") == 1,
      "the manifest is not signed (webOS 26 refuses the old signed one)")
check(sorted(manifest.get("permissions", [])) == ["READ_POWER_STATE", "WRITE_NOTIFICATION_TOAST"],
      f"the driver asks only for pop-ups and the power state ({manifest.get('permissions')})")
check(d.prop("Status") == "Waiting for you to accept on the TV", f"Status while the TV asks: {d.prop('Status')}")
tv.accept()
d.pump(2000)
check(d.prop("Status") == "Paired", f"accepted on the TV: Status Paired ({d.prop('Status')})")
key = next(iter(tv.keys))
saved = d.persisted().get("DL_TV", "")
check(isinstance(saved, str) and saved.startswith("json:") and key in saved, "the client key is saved (json:, survives a driver update)")
check(not d.g.PERSIST_ENC["DL_TV"], "saved in plain persistence (an encrypted value did not survive updates: ADR-028)")
check(c.unmasked == 0, "every frame from the driver is masked (RFC 6455)")
check(len(tv.requests(POWER_URI)) == 1 and tv.requests(POWER_URI)[0]["type"] == "subscribe", "the power state is subscribed after pairing")
check(d.prop("TV") == "OLED55C1PUB · webOS 6.3.0", f"the TV property: model and webOS ({d.prop('TV')})")
check(any("Paired: the TV accepted DirectorLink" in l for l in d.logs()), "the log says the TV accepted")

d26, tv26 = paired(tv=FakeTV(IP, model="OLED65C25LB", webos="11.2.0"))
check(d26.prop("TV") == "OLED65C25LB · webOS 26 (11.2.0)", f"webOS 26 reports release 11.2.0: shown as webOS 26 ({d26.prop('TV')})")

# The ping while connected
d.clear()
before = tv.pings
d.pump(61000)
check(tv.pings - before == 4, f"a ping every 15 s while connected ({tv.pings - before} in 61 s)")
check(d.prop("Status") == "Paired", "pings answered: still Paired")

# ============================================================ the client key again (a restart)
persist = d.persisted()
d2 = Driver(props={"TV IP Address": IP, "Log Level": "Trace"}, persist=persist)
tv.attach(d2)
n_regs = len(tv.registers())
d2.start()
d2.pump(3000)
regs = tv.registers()[n_regs:]
check(len(regs) == 1 and regs[0]["payload"].get("client-key") == key, "after a restart: register with the saved client key")
check(not tv.prompting() and d2.prop("Status") == "Paired", f"no prompt: Paired at once ({d2.prop('Status')})")
secret_free(d2, key, "with the key (Log Level Trace)")
d2.action("PrintDiagnostics")
diag = "\n".join(d2.logs())
check("diagnostics" in diag and "client key saved, never shown" in diag, "Print Diagnostics says a key is saved")
secret_free(d2, key, "Print Diagnostics")

# Director hands back a stored JSON text already decoded into a table (ADR-028): still read
table_persist = {k: (json.loads(v[5:]) if isinstance(v, str) and v.startswith("json:") else v) for k, v in persist.items()}
d3 = Driver(props={"TV IP Address": IP}, persist=table_persist)
tv.attach(d3)
n_regs = len(tv.registers())
d3.start()
d3.pump(3000)
check(tv.registers()[n_regs:] and tv.registers()[n_regs:][0]["payload"].get("client-key") == key and d3.prop("Status") == "Paired",
      "a saved value Director returns as a table is read too")

# ============================================================ the key never in a log (pairing at Trace)
d = Driver(props={"TV IP Address": IP, "Log Level": "Trace"})
tv = FakeTV(IP).attach(d)
d.start()
d.pump(3000)
tv.accept()
d.pump(2000)
key = next(iter(tv.keys))
check(d.prop("Status") == "Paired", "paired at Log Level Trace")
check(any("From the TV:" in l and '"client-key": "***"' in l for l in d.logs()), "Trace shows the TV's 'registered' answer with the key blanked")
d.set_prop("TV IP Address", "", pump_ms=2000)
d.set_prop("TV IP Address", IP, pump_ms=5000)
notify(d)
d.pump(2000)
d.action("PrintDiagnostics")
secret_free(d, key, "pairing, reconnecting and a pop-up at Trace")

# ============================================================ port 3000 when 3001 refuses
tv = FakeTV(IP)
tv.ports[3001] = False
d, tv = paired(tv=tv)
check([p for p, ok in tv.connects][:2] == [3001, 3000], f"3001 refused: port 3000 at once ({tv.connects})")
opts = d.net_log("options")
check(opts[-1][3] == "TCP" and opts[-1][2] == 3000, "port 3000 is plain TCP")
check(d.prop("Status") == "Paired", "paired over port 3000")
persist = d.persisted()
d2 = Driver(props={"TV IP Address": IP}, persist=persist)
tv.connects = []
tv.attach(d2)
d2.start()
d2.pump(3000)
check(tv.connects and tv.connects[0][0] == 3000, f"next time the port that worked is tried first ({tv.connects})")

# ============================================================ declined, unanswered, Pair Again
d = Driver(props={"TV IP Address": IP})
tv = FakeTV(IP).attach(d)
d.start()
d.pump(3000)
tv.decline()
d.pump(2000)
check(d.prop("Status") == "Not accepted on the TV: run Pair Again", f"declined on the TV: Status ({d.prop('Status')})")
n = len(tv.connects)
d.pump(20 * 60000)
check(len(tv.connects) == n, "declined: no new connection, no new prompt (20 minutes)")
notify(d)
d.pump(5000)
check(d.events() == ["Notification Failed"] and not tv.toasts, "a notification while not paired: Notification Failed at once")
persist = d.persisted()
d2 = Driver(props={"TV IP Address": IP}, persist=persist)
tv2 = FakeTV(IP).attach(d2)
d2.start()
d2.pump(10000)
check(not tv2.connects and d2.prop("Status") == "Not accepted on the TV: run Pair Again", "declined, then a restart: still no prompt")
d2.action("PairAgain")
d2.pump(3000)
check(tv2.prompting() and d2.prop("Status") == "Waiting for you to accept on the TV", "Pair Again: the TV asks again")
tv2.accept()
d2.pump(2000)
check(d2.prop("Status") == "Paired", "Pair Again, accepted: Paired")

d = Driver(props={"TV IP Address": IP})
tv = FakeTV(IP).attach(d)
d.start()
d.pump(3000)
check(tv.prompting(), "the TV asks")
d.pump(119000)
check(d.prop("Status") == "Waiting for you to accept on the TV", "still waiting after 2 minutes less a second")
d.pump(2000)
check(d.prop("Status") == "Not accepted on the TV: run Pair Again", f"the TV's own prompt ran out (403): stops asking ({d.prop('Status')})")
check(not tv.conns, "the connection is closed")
n = len(tv.connects)
d.pump(30 * 60000)
check(len(tv.connects) == n, "not accepted in time: no new prompt (30 minutes)")

d = Driver(props={"TV IP Address": IP})
tv = FakeTV(IP)
tv.prompt_timeout_ms = None
tv.attach(d)
d.start()
d.pump(3000)
d.pump(150000)
check(d.prop("Status") == "Not paired: turn the TV on, then run Pair Again", f"a TV that never answers the prompt: the driver stops after 150 s ({d.prop('Status')})")

# A TV that no longer knows the key (reset, or removed in its settings): the prompt again, then a new key
d, tv = paired()
persist = d.persisted()
old = next(iter(tv.keys))
tv.forget_keys()
d2 = Driver(props={"TV IP Address": IP}, persist=persist)
tv.attach(d2)
d2.start()
d2.pump(3000)
check(tv.registers()[-1]["payload"].get("client-key") == old and tv.prompting(), "an old key the TV no longer knows: the TV asks again")
check(any("no longer knows" in l for l in d2.logs()), "the log says the TV no longer knows the key")
tv.accept()
d2.pump(2000)
check(d2.prop("Status") == "Paired" and old not in (d2.persisted().get("DL_TV") or ""), "accepted: the new key replaces the old one")

# A TV that does not answer hello: the driver goes on after 2 s
tv = FakeTV(IP)
tv.answer_hello = False
d, tv = paired(tv=tv, accept=False)
d.pump(2000)
check(tv.prompting(), "no answer to hello: the driver goes on (getSystemInfo, register) after 2 s")
tv.accept()
d.pump(2000)
check(d.prop("Status") == "Paired", "no answer to hello: paired all the same")

# Pair Again forgets the key: a new prompt, a new key
d, tv = paired()
old = next(iter(tv.keys))
d.action("PairAgain")
d.pump(3000)
regs = tv.registers()
check("client-key" not in regs[-1]["payload"] and tv.prompting(), "Pair Again: register without the old key, the TV asks")
check(old not in (d.persisted().get("DL_TV") or ""), "Pair Again: the old key is no longer saved")
tv.accept()
d.pump(2000)
check(d.prop("Status") == "Paired" and len(tv.keys) == 2, "Pair Again: a new key")
check(tv.conn() is not None and tv.conn().registered, "the echo of the old connection's OFFLINE did not end the new one")

# ============================================================ a pop-up
d, tv = paired()
d.clear()
notify(d, title=HE_TITLE, message=HE_MESSAGE, nid="n-1")
check(d.events() == [], "never blocks: DIRECTORLINK_NOTIFY returns before the TV answers")
d.pump(1000)
check(tv.toast_texts() == [f"{HE_TITLE}: {HE_MESSAGE}"], f"the pop-up: title and message, Hebrew exactly as given ({tv.toast_texts()})")
check(d.events() == ["Notification Shown"], f"Notification Shown ({d.events()})")
check(d.event_vars() == [("Notification Shown", f"{HE_TITLE}: {HE_MESSAGE}")], "LAST_NOTIFICATION is set before the event fires")
check(any("[INFO]" in l and "Shown on the TV: Doorbell" in l for l in d.logs()), "one Info line for it")
d.clear()
notify(d, via="ReceivedFromProxy", nid="n-2", title="Alarm", message="Burglary", kind="alarm")
d.pump(6000)
check(tv.toast_texts()[-1] == "Alarm: Burglary" and d.events() == ["Notification Shown"], "DIRECTORLINK_NOTIFY by ReceivedFromProxy too")
d.clear()
d.command("DIRECTORLINK_NOTIFY", {"version": "1", "kind": "DOORBELL", "title": "lower", "message": "case", "id": "n-3"})
d.pump(6000)
check(tv.toast_texts()[-1] == "lower: case", "parameter names and KIND in any case")
d.clear()
notify(d, nid="n-4", VERSION="2")
d.pump(6000)
check(d.events() == ["Notification Shown"], "VERSION 2 is read as version 1 (a later version only adds)")
d.clear()
notify(d, nid="n-5", VERSION="x")
d.pump(6000)
check(d.events() == [] and any("VERSION 'x'" in l for l in d.logs()), "a VERSION that is not a number: ignored, with a warning")
d.clear()
notify(d, nid="n-6", title="", message="")
d.pump(6000)
check(d.events() == [], "nothing to show (no title, no message): nothing sent")
d.clear()
d.command("SHOW_NOTIFICATION", {"Title": "Pizza", "Message": "is here"})
d.pump(6000)
check(tv.toast_texts()[-1] == "Pizza: is here" and d.events() == ["Notification Shown"], "Show Notification (programming)")
check(TOAST_URI in [m.get("uri") for m in tv.messages], "the pop-up is ssap://system.notifications/createToast")
d.clear()
notify(d, nid="pic-1", PICTURE="http://127.0.0.1:47123/notify/abc.jpg")
d.pump(6000)
check(d.events() == ["Notification Shown"] and not d.http_log() and "iconData" not in tv.toasts[-1],
      "a PICTURE is not fetched and not sent (a pop-up with a picture is not shown on many TVs)")

# ============================================================ kinds
d, tv = paired()
d.clear()
notify(d, kind="camera", nid="k-1")
notify(d, kind="door", nid="k-2")
notify(d, kind="other", nid="k-3")
notify(d, kind="spaceship", nid="k-4")
d.pump(30000)
check(not tv.toasts and d.events() == [], "Camera Alerts, Doors and Gates, Other off by default (and an unknown KIND is Other): nothing shown, no event")
d.set_prop("Camera Alerts", "On")
notify(d, kind="camera", nid="k-5", title="Garden", message="Person")
d.pump(1000)
check(tv.toast_texts() == ["Garden: Person"], "Camera Alerts On: shown")
d.set_prop("Doorbell", "Off")
d.clear()
notify(d, kind="doorbell", nid="k-6")
d.pump(6000)
check(len(tv.toasts) == 1 and d.events() == [], "Doorbell Off: a ring is not shown")
d.command("SHOW_NOTIFICATION", {"Title": "Any", "Message": "kind"})
d.pump(6000)
check(tv.toast_texts()[-1] == "Any: kind", "Show Notification is shown whatever the kinds")

# ============================================================ IDs: a repeat within 10 minutes
d, tv = paired()
notify(d, nid="same")
d.pump(6000)
notify(d, nid="same")
d.pump(6000)
check(len(tv.toasts) == 1, "the same ID again within 10 minutes: shown once")
notify(d, nid="other")
d.pump(6000)
check(len(tv.toasts) == 2, "another ID: shown")
d.pump(10 * 60000)
notify(d, nid="same")
d.pump(6000)
check(len(tv.toasts) == 3, "the same ID after 10 minutes: shown again")

# ============================================================ Quiet Hours
d, tv = paired(props={"Quiet Hours": "22:00-07:00"})
d.set_local_time(23, 0)
d.clear()
notify(d, nid="q-1")
d.pump(6000)
check(not tv.toasts and d.events() == [], "Quiet Hours 22:00-07:00, at 23:00: no pop-up, no event")
d.command("SHOW_NOTIFICATION", {"Title": "At", "Message": "night"})
d.pump(6000)
check(not tv.toasts, "Show Notification is quiet too")
d.action("TestNotification")
d.pump(2000)
check(len(tv.toasts) == 1, "Test Notification shows during Quiet Hours (the installer asked)")
d.set_local_time(7, 0)
notify(d, nid="q-2")
d.pump(6000)
check(len(tv.toasts) == 2, "at 07:00: shown again")
d.set_prop("Quiet Hours", "22-7")
check(d.prop("Quiet Hours") == "22:00-07:00", f"Quiet Hours written the plain way ({d.prop('Quiet Hours')})")
d.set_prop("Quiet Hours", "after dinner")
check("Quiet Hours not understood" in d.prop("Status"), f"Quiet Hours not understood: Status says so ({d.prop('Status')})")
d.set_local_time(23, 30)
notify(d, nid="q-3")
d.pump(6000)
check(len(tv.toasts) == 3, "Quiet Hours not understood: not quiet")
d.set_prop("Quiet Hours", "")
check(d.prop("Status") == "Paired", "Quiet Hours cleared: Status Paired")

# ============================================================ one pop-up every 5 seconds, a doorbell or the alarm first
d, tv = paired(props={"Camera Alerts": "On", "Doors and Gates": "On", "Other": "On"})
d.pump(10000)
t0 = d.now()
notify(d, kind="camera", nid="r-1", title="Cam", message="1")
notify(d, kind="door", nid="r-2", title="Door", message="2")
notify(d, kind="doorbell", nid="r-3", title="Bell", message="3")
notify(d, kind="other", nid="r-4", title="Other", message="4")
notify(d, kind="alarm", nid="r-5", title="Alarm", message="5")
d.pump(30000)
texts = tv.toast_texts()
check(texts == ["Cam: 1", "Bell: 3", "Alarm: 5", "Door: 2", "Other: 4"], f"one at a time; the doorbell and the alarm go before those waiting ({texts})")
gaps = d.eval("gNotify.lastSentMs") - t0
check(gaps >= 20000, f"at most one pop-up every 5 s (the fifth went {gaps} ms after the first)")

d, tv = paired(props={"Camera Alerts": "On", "Other": "On"})
d.pump(10000)
d.clear()
for i in range(8):
    notify(d, kind="camera", nid=f"o-{i}", title="Cam", message=str(i))
d.pump(1000)
notify(d, kind="doorbell", nid="o-bell", title="Bell", message="!")
d.pump(60000)
texts = tv.toast_texts()
check("Bell: !" in texts and texts.index("Bell: !") == 1, f"a full queue: the doorbell still gets in, next ({texts})")
check(d.events().count("Notification Failed") >= 3 and len(texts) <= 6, f"a full queue: the extra camera alerts are dropped ({len(texts)} shown, {d.events().count('Notification Failed')} failed)")

# ============================================================ the TV off: standby, gone, not answering
d, tv = paired()
d.clear()
tv.set_power("Active Standby")
d.pump(1000)
check(d.prop("Status") == "Off", f"the TV in standby: Status Off ({d.prop('Status')})")
notify(d, nid="off-1", title="Bell", message="ring")
d.pump(1000)
check(not tv.toasts and d.events() == ["Notification Failed"], "in standby: no pop-up, Notification Failed")
check(d.var("LAST_NOTIFICATION") == "Bell: ring", "LAST_NOTIFICATION is set for a failed one too")
lines = [l for l in d.logs() if "[INFO]" in l and "Not shown" in l]
check(len(lines) == 2 and "in standby" in lines[0], f"one log line (Info) says why ({lines[:1]})")
tv.set_power("Active")
d.pump(1000)
check(d.prop("Status") == "Paired", "on again: Paired")
notify(d, nid="off-2")
d.pump(1000)
check(len(tv.toasts) == 1, "on again: shown")
for state in ("Screen Off", "Suspend", "Power Off"):
    tv.set_power(state)
    d.pump(1000)
    notify(d, nid="off-" + state)
    d.pump(6000)
    check(len(tv.toasts) == 1, f"'{state}': not shown")
tv.power = "Active"
for c in tv.conns.values():
    tv._send(c, {"type": "response", "id": c.subs[POWER_URI], "payload": {"state": "Active", "processing": "Request Power Off", "returnValue": True}})
d.pump(1000)
notify(d, nid="off-going")
d.pump(6000)
check(len(tv.toasts) == 1 and d.prop("Status") == "Off", "on its way off (processing Request Power Off): not shown")
tv.set_power("Screen Saver")
d.pump(1000)
notify(d, nid="saver")
d.pump(6000)
check(len(tv.toasts) == 2 and d.prop("Status") == "Paired", "the screen saver: shown (the TV is on)")
tv.set_power("Active")
d.pump(6000)

# Gone from the network (no answer at all): the ping finds out; a pop-up waits at most 3 s
d.clear()
tv.vanish()
notify(d, nid="gone-1")
d.pump(3100)
check(d.events() == ["Notification Failed"], "the TV gone: Notification Failed within 3 s (no answer to the pop-up)")
check(any("no answer from the TV within 3 s" in l for l in d.logs()), "the log says no answer within 3 s")
check(not [m for m in tv.messages if "turnOn" in str(m.get("uri")) or "wakeOnLan" in str(m.get("uri"))], "the driver never asks the TV to turn on")
check(any("The TV went away (no answer to a pop-up)" in l for l in d.logs()), "a pop-up without an answer ends the connection (made again later)")
d.clear()
tv.connect_times = []
t_gone = d.now()
d.pump(35 * 60000)
check(d.prop("Status") == "Off", f"a TV that answered here before and is gone: Status Off ({d.prop('Status')})")
# The tries: 5 s after the connection ended, then 30 s, 2 min, then every 5 min after each try failed
# (a try waits WS_CONNECT_TIMEOUT_MS, 8 s, for a TV that does not answer)
tries = [(t - t_gone) / 1000 for t in tv.connect_times]
waits = [round(b - a - 8, 1) for a, b in zip(tries, tries[1:])]
check(len(tries) >= 7 and waits[:5] == [30, 120, 300, 300, 300] and set(waits[3:]) == {300},
      f"reconnecting: 5 s, 30 s, 2 min, then every 5 min (waits {waits})")
check(tries and 4.9 <= tries[0] <= 5.1, f"the first try 5 s after the connection ended ({tries[:1]})")

# The ping finds a TV gone without a word (no pop-up to notice it)
d, tv = paired()
tv.vanish()
d.pump(46000)
check(any("The TV went away (the TV stopped answering" in l for l in d.logs()), "a TV gone without a word: the ping finds out within 45 s")
d.clear()
notify(d, nid="gone-2")
d.pump(3100)
check(d.events() == ["Notification Failed"] and any("did not answer within 3 s" in l for l in d.logs()),
      "the TV gone, not connected: a notification tries at once, fails after 3 s")

# Back: the next try connects, Paired, and pop-ups again
tv.come_back()
d.pump(6 * 60000)
check(d.prop("Status") == "Paired", f"the TV back: Paired by the next try ({d.prop('Status')})")
notify(d, nid="back-1")
d.pump(1000)
check(tv.toast_texts()[-1:] == ["Front Door: Someone is at the door"], "back: shown")

# A notification while waiting to try again, with the TV back on: connects at once and shows it
d, tv = paired()
tv.drop()
d.pump(15000)
check(d.eval("gTv.state") == "waiting", "the TV gone: the driver waits for its next try")
tv.come_back()
d.clear()
notify(d, nid="now-1")
d.pump(3000)
check(len(tv.toasts) == 1 and d.events() == ["Notification Shown"], "a notification while waiting to reconnect: tries at once, shown within 3 s")

# The TV does not answer the pop-up (connected, but silent): dropped after 3 s, connection made again
d, tv = paired()
tv.toast_answer = "silent"
d.clear()
notify(d, nid="silent-1")
d.pump(2900)
check(d.events() == [], "no answer yet at 2.9 s")
d.pump(200)
check(d.events() == ["Notification Failed"], "no answer within 3 s: Notification Failed")
tv.toast_answer = "ok"
d.pump(10000)
check(d.prop("Status") == "Paired", "the connection is made again")
tv.toast_answer = "error"
d.clear()
notify(d, nid="err-1")
d.pump(2000)
check(d.events() == ["Notification Failed"] and any("refused the pop-up" in l for l in d.logs()), "the TV refuses the pop-up: Notification Failed")

# An old webOS without the power state: on while connected
tv = FakeTV(IP)
tv.power_service = False
d, tv = paired(tv=tv)
check(d.prop("Status") == "Paired", "no power service (old webOS): Paired")
notify(d, nid="old-1")
d.pump(1000)
check(len(tv.toasts) == 1, "no power service: shown")

# A TV that was never reached at this address: Not reachable
d = Driver(props={"TV IP Address": "192.168.50.99"})
d.start()
d.pump(60000)
check(d.prop("Status") == "Not reachable", f"no TV at the address: Not reachable ({d.prop('Status')})")

# ============================================================ a new address
d, tv = paired()
tv2 = FakeTV("192.168.50.41").attach(d)
d.set_prop("TV IP Address", "192.168.50.41", pump_ms=4000)
check(tv2.prompting() and "client-key" in tv2.registers()[0]["payload"], "a new address: the key is tried there (the same TV, moved)")
tv2.accept()
d.pump(2000)
check(d.prop("Status") == "Paired" and not tv.conns, "a new address: the new TV paired, the old connection closed")

# ============================================================ Find TVs (SSDP)
ssdp = FakeSSDP([{"ip": "192.168.50.40", "friendly": "[LG] webOS TV OLED55C1PUB"}, {"ip": "192.168.50.41", "friendly": "Kids, Room & TV"},
                 {"ip": "192.168.50.42"}, {"ip": "192.168.50.43", "friendly": "Bedroom", "standby": True}])
d = Driver(responder=ssdp.http)
ssdp.attach(d)
tv = FakeTV(IP).attach(d)
d.start()
d.pump(9000)
items = d.prop_list("TVs Found")
check(items == ["-", "[LG] webOS TV OLED55C1PUB (192.168.50.40)", "Kids  Room & TV (192.168.50.41)", "LG TV (192.168.50.42)"],
      f"no address: Find TVs runs and fills TVs Found with each TV's own name ({items})")
check(sorted(set(ssdp.described)) == ["192.168.50.40", "192.168.50.41", "192.168.50.42"], "each TV's description is read once from its own address")
check(len(ssdp.searches) == 3, f"the M-SEARCH is sent 3 times ({len(ssdp.searches)})")
mc = [r for r in d.net_log("create") if r[1] == 6100]
check(mc and mc[0][2] == "239.255.255.250", "the search: UDP to 239.255.255.250:1900")
d.set_prop("TVs Found", "[LG] webOS TV OLED55C1PUB (192.168.50.40)", pump_ms=3000)
check(d.prop("TV IP Address") == IP and tv.prompting(), "picking a TV fills TV IP Address and asks it to pair")

finish()
