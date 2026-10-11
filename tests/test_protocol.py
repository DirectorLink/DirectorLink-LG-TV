"""The parts on their own (offline).   python tests/test_protocol.py

JSON (UTF-8 text through and back, \\u escapes), WebSocket frames (masking, the three
length forms), the SSDP answers, Quiet Hours, the pop-up's text, and the log filter
(a client key never in a log line, also before the driver knows it).
"""
import json

from harness import Driver, check, finish, lua_bytes, utf8

d = Driver()
L = d.lua

# ---- JSON
hebrew = "שער הכניסה נפתח"
enc = utf8(d.call("JsonEncode", d.table({"message": hebrew.encode("utf-8"), "n": 3})))
check(json.loads(enc) == {"message": hebrew, "n": 3}, f"JSON: Hebrew is written as UTF-8 and read back by a standard parser ({enc})")
check("\\u05" not in enc, "JSON: Hebrew is not written as \\u escapes (as given, byte for byte)")
emoji = "🔔 Doorbell"
check(json.loads(utf8(d.call("JsonEncode", d.table({"m": emoji.encode("utf-8")}))))["m"] == emoji, "JSON: emoji (4-byte UTF-8) pass through")
line = utf8(d.call("JsonEncode", d.table({"m": "a\nb\"c\\d\te\x01".encode()})))
check(json.loads(line)["m"] == "a\nb\"c\\d\te\x01", f"JSON: line break, quote, backslash, tab and control bytes escaped ({line})")
dec = d.call("JsonDecode", '{"payload":{"state":"Active Standby","x":"\\u05e9\\u05e2\\u05e8","e":"\\ud83d\\udd14"}}')
check(utf8(dec.payload.x) == "שער" and utf8(dec.payload.e) == "🔔", "JSON: \\u escapes (and a surrogate pair) are decoded to UTF-8")
bad = d.call("JsonDecode", "{bad")
check(isinstance(bad, tuple) and bad[0] is None and bad[1], f"JSON: a broken message is refused, not half read ({bad})")
check(utf8(d.call("JsonEncode", d.call("JsonObject", None))) == "{}", "JSON: an empty object is {}")

# ---- WebSocket frames (client frames: masked)
def unmask(frame):
    b = lua_bytes(frame)
    n, pos = b[1] & 0x7F, 2
    if n == 126:
        n, pos = int.from_bytes(b[2:4], "big"), 4
    elif n == 127:
        n, pos = int.from_bytes(b[2:10], "big"), 10
    key = b[pos:pos + 4]
    data = b[pos + 4:]
    return b[0], bool(b[1] & 0x80), n, bytes(x ^ key[i % 4] for i, x in enumerate(data))


for size in (0, 5, 125, 126, 300, 65535, 65536, 70001):
    payload = bytes((i * 7) % 256 for i in range(size))
    first, masked, n, data = unmask(d.call("WsFrame", 1, payload))
    check(first == 0x81 and masked and n == size and data == payload, f"WebSocket: a {size}-byte text frame is FIN, masked, and unmasks to the payload")
first, masked, n, data = unmask(d.call("WsFrame", 9, b"dl"))
check(first == 0x89 and masked and data == b"dl", "WebSocket: a ping frame")
key = b"\x01\x80\xff\x10"
check(lua_bytes(d.call("WsMask", d.call("WsMask", b"hello world!", key), key)) == b"hello world!", "WebSocket: masking twice gives the data back")

# ---- SSDP answers
answer = (b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\nLOCATION: http://192.168.1.40:1691/\r\n"
          b"SERVER: WebOS/4.1.0 UPnP/1.0\r\nST: urn:lge-com:service:webos-second-screen:1\r\n"
          b"USN: uuid:1d0ac2be-1111::urn:lge-com:service:webos-second-screen:1\r\n"
          b"DLNADeviceName.lge.com: %5BLG%5D%20webOS%20TV%20OLED55C1PUB\r\n\r\n"
          b"HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.41:1691/\r\nST: urn:lge-com:service:webos-second-screen:1\r\n\r\n"
          b"HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.50:1400/xml/device_description.xml\r\nST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n\r\n"
          b"HTTP/1.1 200 OK\r\nLOCATION: http://8.8.8.8:1691/\r\nST: urn:lge-com:service:webos-second-screen:1\r\n\r\n")
replies = [dict(r) for r in d.call("SsdpReplies", answer).values()]
check([r["ip"] for r in replies] == ["192.168.1.40", "192.168.1.41"], f"SSDP: the LG TVs in one datagram, not a Sonos, not a public address ({[r['ip'] for r in replies]})")
check(utf8(replies[0]["name"]) == "[LG] webOS TV OLED55C1PUB" and replies[1].get("name") is None, "SSDP: the TV's name from DLNADeviceName.lge.com, URL-decoded")
req = utf8(d.call("SsdpRequest"))
check(req.startswith("M-SEARCH * HTTP/1.1\r\n") and "ST: urn:lge-com:service:webos-second-screen:1\r\n" in req and 'MAN: "ssdp:discover"' in req
      and req.endswith("\r\n\r\n"), "SSDP: the M-SEARCH for webOS TVs")

# ---- Quiet Hours
def qh(text):
    r = d.call("ParseQuietHours", text.encode("utf-8"))
    return r if not isinstance(r, tuple) else tuple(r)


check(qh("") is None and qh("   ") is None, "Quiet Hours: empty is none")
check(qh("22:00-07:00") == (1320, 420), "Quiet Hours: 22:00-07:00")
check(qh("22-7") == (1320, 420) and qh("22:30 - 06:45") == (1350, 405) and qh("22.00–07.00") == (1320, 420)
      and qh("2200-0700") == (1320, 420) and qh("13:00—15:00") == (780, 900), "Quiet Hours: 22-7, spaces, dots, an en or em dash, 2200-0700")
check(qh("24:00-06:00") == (0, 360), "Quiet Hours: 24:00 is midnight")
for bad in ("22:00", "evening", "25:00-07:00", "22:61-07:00", "22:00-22:00", "22:00 to 07:00"):
    check(qh(bad) is False, f"Quiet Hours: '{bad}' is not understood")
L.globals().Properties["Quiet Hours"] = "22:00-07:00"
check(d.call("InQuietHours", 22 * 60) and d.call("InQuietHours", 23 * 60 + 59) and d.call("InQuietHours", 0)
      and d.call("InQuietHours", 6 * 60 + 59), "Quiet Hours: 22:00, 23:59, midnight and 06:59 are quiet (across midnight)")
check(not d.call("InQuietHours", 7 * 60) and not d.call("InQuietHours", 21 * 60 + 59) and not d.call("InQuietHours", 12 * 60),
      "Quiet Hours: 07:00, 21:59 and noon are not")
L.globals().Properties["Quiet Hours"] = "13:00-15:00"
check(d.call("InQuietHours", 13 * 60) and not d.call("InQuietHours", 15 * 60) and not d.call("InQuietHours", 22 * 60), "Quiet Hours: 13:00-15:00 within a day")
L.globals().Properties["Quiet Hours"] = "nonsense"
check(not d.call("InQuietHours", 23 * 60), "Quiet Hours: not understood is none (never everything)")
L.globals().Properties["Quiet Hours"] = ""

# ---- The pop-up's text
def toast(t, m):
    return utf8(d.call("ToastText", t.encode("utf-8"), m.encode("utf-8")))


check(toast("Front Door", "Someone is at the door") == "Front Door: Someone is at the door", "Text: title and message joined")
check(toast("", "Only a message") == "Only a message" and toast("Only a title", "") == "Only a title", "Text: one of them alone")
check(toast("  שער  ", " נפתח ") == "שער: נפתח", "Text: Hebrew title and message, as given (spaces around trimmed)")
long = "א" * 800
cut = lua_bytes(d.call("ToastText", b"", long.encode("utf-8")))
check(cut.endswith(b"...") and len(cut) <= 1003 and cut[:-3].decode("utf-8") == "א" * (len(cut[:-3]) // 2),
      "Text: a very long message is cut at a whole character")

# ---- The log filter
d.call("RegisterSecret", "0123456789abcdef0000000000000001")
red = d.call("Redact", 'sent {"client-key":"0123456789abcdef0000000000000001"} and 0123456789abcdef0000000000000001')
check("0123456789abcdef" not in red, f"Log: a registered key is never in a line ({red})")
red = d.call("Redact", 'From the TV: {"type":"registered","payload":{"client-key": "f00dcafe-not-known-yet"}}')
check("f00dcafe" not in red and '"client-key": "***"' in red, f"Log: a client key the driver does not know yet is blanked ({red})")

finish()
