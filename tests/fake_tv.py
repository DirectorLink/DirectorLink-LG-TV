"""A webOS TV for the tests: the TV's side of LG's local WebSocket API (SSAP), as the
open-source clients (aiowebostv, LGWebOSRemote, lgtv2) see it, over the harness's
fake network.

- Port 3001 (wss) and 3000 (ws) can each be open, closed (connection refused) or the
  whole TV gone from the network (no answer at all: off, unplugged).
- The WebSocket upgrade (Sec-WebSocket-Accept), frames in both directions; every frame
  from the driver must be masked (RFC 6455), pings are answered.
- "hello" (answered without an id, as TVs do) and getSystemInfo, also before registering.
- Pairing: "register" without a known client key shows the prompt
  ({"type":"response","payload":{"pairingType":"PROMPT"}}); accept() answers
  {"type":"registered","payload":{"client-key":...}}, decline() "403 User denied access",
  and so does the TV itself when nobody answers within prompt_timeout_ms (about two
  minutes on a real TV). A known key is registered at once; an unknown one brings the prompt.
- getPowerState (subscribe: every change is sent) and createToast (answered, refused,
  or not answered), only with the permission asked for when pairing.
"""
import base64
import hashlib
import json
import re

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
POWER_URI = "ssap://com.webos.service.tvpower/power/getPowerState"
TOAST_URI = "ssap://system.notifications/createToast"
SYSINFO_URI = "ssap://system/getSystemInfo"


def server_frame(opcode, payload):
    """A frame from the TV (never masked)."""
    n = len(payload)
    if n <= 125:
        head = bytes([0x80 | opcode, n])
    elif n <= 65535:
        head = bytes([0x80 | opcode, 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 127]) + n.to_bytes(8, "big")
    return head + payload


class Conn:
    def __init__(self, binding, port):
        self.binding, self.port = binding, port
        self.phase = "http"
        self.buf = b""
        self.registered = False
        self.key = None
        self.prompt_id = None
        self.subs = {}            # uri -> request id
        self.request_head = None  # the upgrade request, as text
        self.unmasked = 0         # frames from the driver that were not masked (must stay 0)
        self.fragments = None


class FakeTV:
    def __init__(self, ip="192.168.50.40", model="OLED55C1PUB", webos="6.3.0"):
        self.ip = ip
        self.model, self.webos = model, webos        # webos: hello's deviceOSReleaseVersion
        self.ports = {3001: True, 3000: True}
        self.network = True        # False: the TV does not answer at all (off without Quick Start+, unplugged)
        self.power = "Active"      # "Active", "Active Standby", "Screen Off", "Suspend", ...
        self.power_service = True  # False: an old webOS without getPowerState
        self.keys = set()          # client keys it accepted
        self.prompt = "manual"     # "manual" | "accept" | "decline" | "ignore": what the person at the TV does
        self.accept_after_ms = 3000
        self.prompt_timeout_ms = 120000  # the TV's own prompt: then "403 User denied access" (None: nothing)
        self.answer_hello = True
        self.granted = {}          # client key -> the permissions asked for when it was given
        self.toast_answer = "ok"   # "ok" | "error" | "silent"
        self.latency = 20
        self.conns = {}            # binding -> Conn
        self.toasts = []           # the createToast payloads it got (dicts, text decoded)
        self.messages = []         # every message from the driver (dicts)
        self.connects = []         # (port, accepted)
        self.connect_times = []    # when each connection was tried (ms, the virtual clock)
        self.pings = 0
        self.next_key = 1
        self.d = None

    def attach(self, d):
        """Listen on the driver's fake network at this TV's address (both ports)."""
        self.d = d
        for port in (3001, 3000):
            d.listen(self.ip, port, self)
        return self

    # ---- the network
    def net(self, d, op, binding, port, data):
        self.d = d
        if op == "connect":
            self.connect_times.append(d.now())
            if not self.network:
                self.connects.append((port, None))
                return  # nothing comes back: Director's attempt times out
            if not self.ports.get(port):
                self.connects.append((port, False))
                d.deliver(self.latency, "status", binding, port, "OFFLINE")
                return
            self.connects.append((port, True))
            self.conns[binding] = Conn(binding, port)
            d.deliver(self.latency, "status", binding, port, "ONLINE")
        elif op == "disconnect":
            c = self.conns.pop(binding, None)
            if c is not None and self.network:
                d.deliver(self.latency, "status", binding, port, "OFFLINE")
        elif op == "send":
            c = self.conns.get(binding)
            if c is None or not self.network:
                return
            c.buf += data
            self._process(c)

    def _send(self, c, payload, opcode=1, delay=None):
        if not self.network or self.conns.get(c.binding) is not c:
            return
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.d.deliver(self.latency if delay is None else delay, "data", c.binding, c.port, server_frame(opcode, payload))

    def _raw(self, c, data):
        self.d.deliver(self.latency, "data", c.binding, c.port, data)

    def _process(self, c):
        if c.phase == "http":
            end = c.buf.find(b"\r\n\r\n")
            if end < 0:
                return
            head, c.buf = c.buf[:end].decode("latin-1"), c.buf[end + 4:]
            c.request_head = head
            headers = dict((k.lower(), v.strip()) for k, v in re.findall(r"\r\n([^:\r\n]+):([^\r\n]*)", head))
            key = headers.get("sec-websocket-key", "").encode()
            if not head.startswith("GET / HTTP/1.1") or headers.get("upgrade", "").lower() != "websocket" or not key:
                self._raw(c, b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                return
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
            self._raw(c, b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            c.phase = "ws"
        while c.phase == "ws":
            frame = self._frame(c)
            if frame is None:
                return
            fin, opcode, payload = frame
            if opcode == 9:
                self.pings += 1
                self._send(c, payload, opcode=10)
            elif opcode == 8:
                self._send(c, payload[:2], opcode=8)
                self.conns.pop(c.binding, None)
                self.d.deliver(self.latency * 2, "status", c.binding, c.port, "OFFLINE")
                return
            elif opcode in (1, 0):
                if opcode == 1 and not fin:
                    c.fragments = [payload]
                    continue
                if opcode == 0:
                    c.fragments.append(payload)
                    if not fin:
                        continue
                    payload, c.fragments = b"".join(c.fragments), None
                self._message(c, json.loads(payload.decode("utf-8")))

    def _frame(self, c):
        b = c.buf
        if len(b) < 2:
            return None
        fin, opcode = bool(b[0] & 0x80), b[0] & 0x0F
        masked, n, pos = bool(b[1] & 0x80), b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, pos = int.from_bytes(b[2:4], "big"), 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, pos = int.from_bytes(b[2:10], "big"), 10
        if not masked:
            c.unmasked += 1
        mask = b[pos:pos + 4] if masked else b"\0\0\0\0"
        if masked:
            pos += 4
        if len(b) < pos + n:
            return None
        payload = bytes(x ^ mask[i % 4] for i, x in enumerate(b[pos:pos + n]))
        c.buf = b[pos + n:]
        return fin, opcode, payload

    # ---- SSAP
    def _message(self, c, msg):
        self.messages.append(msg)
        mtype, mid, uri = msg.get("type"), msg.get("id"), msg.get("uri")
        if mtype == "hello":
            if self.answer_hello:
                self._send(c, {"type": "hello", "payload": {
                    "protocolVersion": 1, "deviceType": "tv", "deviceOS": "webOS", "deviceOSVersion": "4.1.0",
                    "deviceOSReleaseVersion": self.webos, "deviceUUID": "f37f2ecd-0000-5a86-898f-0bdf54b3ac1b",
                    "pairingTypes": ["PIN", "PROMPT", "COMBINED"]}})
            return
        if mtype == "register":
            payload = msg.get("payload") or {}
            key = payload.get("client-key")
            c.asked = list((payload.get("manifest") or {}).get("permissions") or [])
            if key and key in self.keys:
                c.registered, c.key = True, key
                self._send(c, {"type": "registered", "id": mid, "payload": {"client-key": key}})
                return
            c.prompt_id = mid
            self._send(c, {"type": "response", "id": mid, "payload": {"pairingType": "PROMPT", "returnValue": True}})
            if self.prompt == "accept":
                self._later(c, self.accept_after_ms, lambda: self.accept())
            elif self.prompt == "decline":
                self._later(c, self.accept_after_ms, lambda: self.decline())
            if self.prompt_timeout_ms:
                self._later(c, self.prompt_timeout_ms, lambda: self.decline(only=c))
            return
        if mtype not in ("request", "subscribe"):
            self._send(c, {"type": "error", "id": mid, "error": "400 unknown message type", "payload": {}})
            return
        if uri == SYSINFO_URI:
            self._send(c, {"type": "response", "id": mid, "payload": {
                "returnValue": True, "features": {"dvr": True}, "receiverType": "dvb",
                "modelName": self.model, "serialNumber": "304MAXXXXXXX", "programMode": True}})
            return
        if not c.registered:
            self._send(c, {"type": "error", "id": mid, "error": "401 insufficient permissions (not registered)", "payload": {}})
            return
        payload = msg.get("payload") or {}
        needs = {POWER_URI: "READ_POWER_STATE", TOAST_URI: "WRITE_NOTIFICATION_TOAST"}.get(uri)
        if needs and needs not in self.granted.get(c.key, []):
            self._send(c, {"type": "error", "id": mid, "error": "401 insufficient permissions", "payload": {}})
            return
        if uri == POWER_URI:
            if not self.power_service:
                self._send(c, {"type": "error", "id": mid, "error": "404 no such service or method", "payload": {}})
                return
            if mtype == "subscribe":
                c.subs[POWER_URI] = mid
            self._send(c, {"type": "response", "id": mid, "payload": dict(self._power_payload(), subscribed=(mtype == "subscribe"))})
        elif uri == TOAST_URI:
            self.toasts.append(payload)
            if self.toast_answer == "ok":
                self._send(c, {"type": "response", "id": mid, "payload": {"returnValue": True, "toastId": f"com.webos.service.apiadapter-{len(self.toasts)}"}})
            elif self.toast_answer == "error":
                self._send(c, {"type": "error", "id": mid, "error": "500 Application error", "payload": {"returnValue": False, "errorText": "toast refused"}})
        else:
            self._send(c, {"type": "error", "id": mid, "error": "404 no such service or method", "payload": {}})

    def _power_payload(self):
        return {"returnValue": True, "state": self.power}

    def _later(self, c, ms, fn):
        """Do fn after ms on the virtual clock (a timer in the fake Director)."""
        self._seq = getattr(self, "_seq", 0) + 1
        name = f"PY_LATER_{self._seq}"
        self.d.g[name] = lambda *_: fn()
        self.d.run(f"C4:SetTimer({int(ms)}, function() {name}() end)")

    # ---- the person at the TV, and the TV
    def prompting(self):
        return [c for c in self.conns.values() if c.prompt_id is not None and not c.registered]

    def accept(self):
        for c in self.prompting():
            key = f"0123456789abcdef{self.next_key:016x}"
            self.next_key += 1
            self.keys.add(key)
            self.granted[key] = list(getattr(c, "asked", []))
            c.registered, c.key = True, key
            self._send(c, {"type": "registered", "id": c.prompt_id, "payload": {"client-key": key}})
            c.prompt_id = None

    def decline(self, only=None):
        """The person says no (or, with only=, the TV's own prompt ran out on that connection)."""
        for c in self.prompting():
            if only is not None and c is not only:
                continue
            self._send(c, {"type": "error", "id": c.prompt_id, "error": "403 User denied access", "payload": {}})
            c.prompt_id = None

    def forget_keys(self):
        """The TV no longer knows any app (reset, or removed in its settings)."""
        self.keys.clear()

    def set_power(self, state):
        """The TV's power state changes; subscribers hear it."""
        self.power = state
        for c in list(self.conns.values()):
            mid = c.subs.get(POWER_URI)
            if mid:
                self._send(c, {"type": "response", "id": mid, "payload": dict(self._power_payload(), subscribed=True)})

    def vanish(self):
        """The TV goes off the network without a word (power cut, off without Quick Start+)."""
        self.network = False
        self.conns.clear()

    def drop(self):
        """The TV closes its connections (it went to standby and its network went down cleanly)."""
        for c in list(self.conns.values()):
            self.d.deliver(self.latency, "status", c.binding, c.port, "OFFLINE")
        self.conns.clear()
        self.network = False

    def come_back(self, power="Active"):
        self.network = True
        self.power = power

    def conn(self):
        return next(iter(self.conns.values()), None)

    def registers(self):
        return [m for m in self.messages if m.get("type") == "register"]

    def requests(self, uri=None):
        return [m for m in self.messages if m.get("type") in ("request", "subscribe") and (uri is None or m.get("uri") == uri)]

    def toast_texts(self):
        return [t.get("message") for t in self.toasts]


class FakeSSDP:
    """The SSDP search's answers: each TV listed answers each M-SEARCH (unicast, to the search's binding),
    and serves its description (friendlyName) at its LOCATION: use .http as the driver's responder."""

    def __init__(self, tvs):
        self.tvs = tvs  # list of dicts: ip, friendly (the description's name), name (DLNADeviceName, URL-encoded), port
        self.searches = []
        self.described = []

    def http(self, method, url, headers, body):
        for tv in self.tvs:
            if url == f"http://{tv['ip']}:{tv.get('port', 1691)}/" and method == "GET":
                self.described.append(tv["ip"])
                if not tv.get("friendly"):
                    return 404, {}, ""
                name = tv["friendly"].replace("&", "&amp;").replace("<", "&lt;")
                return 200, {"Content-Type": "text/xml"}, (
                    '<?xml version="1.0" encoding="utf-8"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
                    f"<deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType><friendlyName>{name}</friendlyName>"
                    "<manufacturer>LG Electronics</manufacturer><modelName>LG Smart TV</modelName>"
                    "<UDN>uuid:1d0ac2be-0000</UDN></device></root>").encode("utf-8")
        return None, None, None

    def attach(self, d):
        d.listen("239.255.255.250", 1900, self)
        return self

    def net(self, d, op, binding, port, data):
        if op == "connect":
            d.deliver(5, "status", binding, port, "ONLINE")
        elif op == "send":
            text = data.decode("latin-1")
            self.searches.append(text)
            if "ST: urn:lge-com:service:webos-second-screen:1" not in text:
                return
            for i, tv in enumerate(self.tvs):
                if tv.get("standby"):
                    continue
                reply = (
                    "HTTP/1.1 200 OK\r\n"
                    "CACHE-CONTROL: max-age=1800\r\n"
                    "DATE: Sun, 11 Oct 2026 18:00:00 GMT\r\n"
                    "EXT:\r\n"
                    f"LOCATION: http://{tv['ip']}:{tv.get('port', 1691)}/\r\n"
                    "SERVER: WebOS/4.1.0 UPnP/1.0\r\n"
                    "ST: urn:lge-com:service:webos-second-screen:1\r\n"
                    f"USN: uuid:{tv.get('uuid', '1d0ac2be-0000-1000-8000-00000000000' + str(i))}::urn:lge-com:service:webos-second-screen:1\r\n"
                    + (f"DLNADeviceName.lge.com: {tv['name']}\r\n" if tv.get("name") else "")
                    + "\r\n"
                )
                d.deliver(50 + 30 * i, "data", binding, port, reply.encode("latin-1"))
