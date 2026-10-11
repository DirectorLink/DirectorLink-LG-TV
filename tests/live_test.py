"""Live test: the real driver code against a real LG TV, from a computer on the same network.

    python tests/live_test.py find            # Find TVs: the driver's own SSDP search, over real UDP
    python tests/live_test.py <TV IP>         # pair (accept on the TV), Print Diagnostics, pop-ups

Runs the assembled driver.lua in the harness (Lua 5.1 and the fake Director) with its
network bridged to real sockets: TLS to the TV on port 3001 (or TCP on 3000), UDP for
the search, HTTP for a TV's description. The driver's clock follows the real one.

This computer pairs with the TV as a device of its own (the TV asks once): its client
key is kept in ~/.directorlink/lg-tv-live.json, never in the repository. The log
lines printed are the driver's own, so the key never shows. Not part of CI.
"""
import json
import os
import select
import socket
import ssl
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from harness import Driver, lua_bytes

STATE = os.path.join(os.path.expanduser("~"), ".directorlink", "lg-tv-live.json")
HE_TITLE, HE_MESSAGE = "דלת הכניסה", "מישהו מצלצל בדלת"


class Bridge:
    """Director's network connections, made real."""

    def __init__(self, d, ip=None):
        self.d = d
        self.socks = {}  # binding -> dict(sock, port, udp, dest)
        if ip:
            d.listen(ip, 3001, self)
            d.listen(ip, 3000, self)
        d.listen("239.255.255.250", 1900, self)

    def net(self, d, op, binding, port, data):
        host = lua_bytes(d.g.NET_HOSTS[binding]).decode()
        if op == "connect":
            if host == "239.255.255.250":
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                s.bind(("", 0))
                s.setblocking(False)
                self.socks[binding] = {"sock": s, "port": port, "udp": True, "dest": (host, port)}
                d.deliver(0, "status", binding, port, "ONLINE")
                return
            try:
                raw = socket.create_connection((host, port), timeout=4)
                if port == 3001:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE  # every LG TV has LG's own certificate, with no address in it
                    raw = ctx.wrap_socket(raw)
                raw.setblocking(False)
                self.socks[binding] = {"sock": raw, "port": port, "udp": False}
                print(f"   [net] connected to {host}:{port}" + (" (TLS)" if port == 3001 else ""))
                d.deliver(0, "status", binding, port, "ONLINE")
            except OSError as e:
                print(f"   [net] {host}:{port}: {e}")
                d.deliver(0, "status", binding, port, "OFFLINE")
        elif op == "send":
            c = self.socks.get(binding)
            if not c:
                return
            if c["udp"]:
                c["sock"].sendto(data, c["dest"])
            else:
                c["sock"].setblocking(True)
                try:
                    c["sock"].sendall(data)
                finally:
                    c["sock"].setblocking(False)
        elif op == "disconnect":
            c = self.socks.pop(binding, None)
            if c:
                c["sock"].close()
                if not c["udp"]:
                    d.deliver(0, "status", binding, port, "OFFLINE")

    def poll(self, timeout=0.05):
        if not self.socks:
            time.sleep(timeout)
            return
        by_sock = {c["sock"]: (b, c) for b, c in self.socks.items()}
        ready, _, _ = select.select(list(by_sock), [], [], timeout)
        for s in ready:
            b, c = by_sock[s]
            while True:
                try:
                    data = s.recv(65536)
                except (BlockingIOError, ssl.SSLWantReadError):
                    break
                except OSError as e:
                    print(f"   [net] read: {e}")
                    data = b""
                if not data:
                    self.socks.pop(b, None)
                    s.close()
                    self.d.deliver(0, "status", b, c["port"], "OFFLINE")
                    break
                self.d.deliver(0, "data", b, c["port"], data)
                if c["udp"] or not (isinstance(s, ssl.SSLSocket) and s.pending()):
                    if c["udp"]:
                        continue
                    break


def real_http(method, url, headers, body):
    """C4:url for a TV's description: only an address on the home network."""
    host = urlsplit(url).hostname or ""
    if not host.startswith(("192.168.", "10.", "172.")):
        return None, None, None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=3) as r:
            return r.status, {}, r.read()
    except Exception as e:  # noqa: BLE001
        print(f"   [http] {url}: {e}")
        return None, None, None


class Live:
    def __init__(self, props, persist=None, ip=None):
        # The driver's clock and time zone are this computer's
        offset_h = round(time.localtime().tm_gmtoff / 3600)
        self.d = Driver(props=props, persist=persist, responder=real_http, now_ms=int(time.time() * 1000), tz_offset_h=offset_h)
        self.net = Bridge(self.d, ip)
        self.seen_logs = 0
        self.last = time.time()
        self.d.start()

    def logs(self):
        lines = self.d.logs()
        new, self.seen_logs = lines[self.seen_logs:], len(lines)
        prev = None
        for line in new:
            if line != prev:  # print and C4:DebugLog carry the same line
                print("   " + line)
            prev = line

    def run(self, seconds, until=None):
        end = time.time() + seconds
        while time.time() < end:
            self.net.poll()
            now = time.time()
            self.d.pump(int((now - self.last) * 1000))
            self.last = now
            self.logs()
            if until and until():
                return True
        return False


def find():
    live = Live({"Log Level": "Debug"})
    live.run(10)
    print("\nTVs Found:", live.d.prop_list("TVs Found"))


def pair_and_show(ip):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    persist = {}
    if os.path.exists(STATE):
        saved = json.load(open(STATE, encoding="utf-8"))
        persist = saved.get(ip, {})
    live = Live({"TV IP Address": ip, "Log Level": "Debug"}, persist=persist, ip=ip)
    d = live.d

    def status():
        return d.prop("Status")

    print(f"\n== Connecting to {ip}")
    live.run(10, until=lambda: status() in ("Paired", "Off") or status().startswith("Not") or status().startswith("Waiting"))
    if status().startswith("Waiting"):
        print("\n>>> The TV asks to allow a new device: ACCEPT IT WITH THE TV'S REMOTE (note the words on the screen)\n")
        live.run(150, until=lambda: not status().startswith("Waiting"))
    print(f"\n== Status: {status()}   TV: {d.prop('TV')}")
    saved = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    saved[ip] = {k: v for k, v in d.persisted().items() if isinstance(v, str)}
    json.dump(saved, open(STATE, "w", encoding="utf-8"))
    d.action("PrintDiagnostics")
    live.logs()
    if status() != "Paired":
        print("\nNot paired, or the TV is off: no pop-ups to try.")
        return
    print("\n== Test Notification")
    d.action("TestNotification")
    live.run(6, until=lambda: d.events())
    print("\n== DIRECTORLINK_NOTIFY doorbell, in Hebrew")
    d.command("DIRECTORLINK_NOTIFY", {"VERSION": "1", "ID": f"live-{int(time.time())}", "KIND": "doorbell",
                                      "TITLE": HE_TITLE, "MESSAGE": HE_MESSAGE})
    live.run(9, until=lambda: len(d.events()) >= 2)
    print("\n== Events:", d.events(), "\n== LAST_NOTIFICATION:", d.var("LAST_NOTIFICATION"))
    d.action("PrintDiagnostics")
    live.logs()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "find":
        find()
    else:
        pair_and_show(sys.argv[1])
