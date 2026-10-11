"""In-process test harness: runs the driver's assembled Lua in a real Lua 5.1 runtime
(lupa) against a fake Director. No network access.

- Network connections (C4:CreateNetworkConnection, C4:NetConnect, C4:SendToNetwork,
  C4:NetDisconnect) go to Python fakes (a webOS TV in fake_tv.py, the SSDP answers):
  `Driver.net` routes each (address, port) to the fake listening there. A fake answers
  with d.deliver(delay_ms, ...): Director's OnConnectionStatusChanged and
  ReceivedFromNetwork, at that time on the virtual clock.
- C4:url (the notification picture) is answered by a Python responder(method, url, headers, body).
- The clock is virtual: NOW_MS starts at 2026-10-11 18:00 UTC and d.pump(ms) runs the timers
  and the network deliveries in the order they fall due, moving the clock to each, up to
  ms from now. The controller's time zone is fixed (TZ_OFFSET_S, UTC+3 by default) so
  local times (Quiet Hours) are the same on every machine.
- Every log line is captured (print, C4:DebugLog, C4:ErrorLog).
"""
import base64
import calendar
import datetime
import hashlib
import os
import re
import sys

import lupa.lua51 as lupa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # test labels hold Hebrew
except AttributeError:
    pass
import build  # noqa: E402  (assemble_lua)

START_MS = calendar.timegm((2026, 10, 11, 18, 0, 0)) * 1000

STUB = r"""
EVENTS = {}; VARS = {}; TIMERS = {}; PERSIST = {}; PERSIST_ENC = {}; ATTRIBS = {}; PROP_LISTS = {}
HTTP_LOG = {}; LOGS = {}; HTTP_QUEUE = {}; EVENT_VARS = {}; VAR_LOG = {}; NET_LOG = {}; NET_HOSTS = {}
DELIVERIES = {}
DEVICE_ID = 900; CONTROLLER_IP = "192.168.50.10"; OS_VERSION = "3.4.3.123"; DISPLAY_NAME = "Living Room TV"
NOW_MS = START_MS; HTTP_ASYNC = false; TZ_OFFSET_S = 3 * 3600; UUIDS = 0
local seq = 0
C4 = {}
VAR_ORDER = {}
function C4:AddVariable(n, v) if VARS[n] == nil then VAR_ORDER[#VAR_ORDER + 1] = n end; VARS[n] = v; return 1, true end
function C4:SetVariable(n, v) VARS[n] = v; VAR_LOG[#VAR_LOG + 1] = n end
function C4:FireEvent(n) EVENTS[#EVENTS + 1] = n; EVENT_VARS[#EVENT_VARS + 1] = { n, VARS["LAST_NOTIFICATION"] } end
function C4:UpdateProperty(n, v) Properties[n] = v end
function C4:UpdatePropertyList(n, items) PROP_LISTS[n] = items end
function C4:SetPropertyAttribs(n, v) ATTRIBS[n] = v end
function C4:GetDeviceID() return DEVICE_ID end
function C4:GetDeviceDisplayName(id) return DISPLAY_NAME end
function C4:GetControllerNetworkAddress() return CONTROLLER_IP end
function C4:GetVersionInfo() return { version = OS_VERSION } end
function C4:GetTime() return NOW_MS end
function C4:GetDriverConfigInfo() return "1000001" end
function C4:UUID(kind) UUIDS = UUIDS + 1; return string.format("%08x-1234-4abc-9def-%012x", UUIDS * 2654435761 % 4294967296, UUIDS * 40503) end
function C4:PersistSetValue(n, v, enc) PERSIST[n] = v; PERSIST_ENC[n] = enc and true or false end
function C4:PersistGetValue(n) return PERSIST[n] end
function C4:PersistDeleteValue(n) PERSIST[n] = nil end
function C4:DebugLog(s) LOGS[#LOGS + 1] = s end
function C4:ErrorLog(s) LOGS[#LOGS + 1] = s end
function C4:Hash(alg, s, opts) return PY_HASH(alg, s, opts and opts.return_encoding or "HEX") end
local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
function C4:Base64Encode(s)  -- pure Lua: binary data must not cross into Python as text
  local out = {}
  for i = 1, #s, 3 do
    local a, b, c = string.byte(s, i, i + 2)
    local n = a * 65536 + (b or 0) * 256 + (c or 0)
    local c1, c2 = math.floor(n / 262144) % 64, math.floor(n / 4096) % 64
    local c3, c4 = math.floor(n / 64) % 64, n % 64
    out[#out + 1] = B64:sub(c1 + 1, c1 + 1) .. B64:sub(c2 + 1, c2 + 1)
      .. (b and B64:sub(c3 + 1, c3 + 1) or "=") .. (c and B64:sub(c4 + 1, c4 + 1) or "=")
  end
  return table.concat(out)
end
-- Local time: UTC plus TZ_OFFSET_S, whatever the machine's time zone
local realDate = os.date
os.date = function(fmt, t)
  fmt = fmt or "%c"
  t = t or math.floor(NOW_MS / 1000)
  if string.sub(fmt, 1, 1) == "!" then return realDate(fmt, t) end
  return realDate("!" .. fmt, t + TZ_OFFSET_S)
end
os.time = function(t) if t then return PY_MKTIME(t.year, t.month, t.day, t.hour or 12, t.min or 0, t.sec or 0) end return math.floor(NOW_MS / 1000) end
function C4:SetTimer(ms, fn, rep)
  seq = seq + 1
  local t = { id = seq, ms = ms, due = NOW_MS + ms, fn = fn, rep = rep, active = true }
  function t:Cancel() self.active = false end
  TIMERS[#TIMERS + 1] = t
  return t
end
-- Network: Director's side of a connection, answered by the Python fakes
function C4:CreateNetworkConnection(b, host, kind) NET_HOSTS[b] = host; NET_LOG[#NET_LOG + 1] = { "create", b, host, kind } end
function C4:NetPortOptions(b, port, kind, opts) NET_LOG[#NET_LOG + 1] = { "options", b, port, kind, opts } end
function C4:NetConnect(b, port) NET_LOG[#NET_LOG + 1] = { "connect", b, port }; PY_NET("connect", b, port, NET_HOSTS[b], nil) end
function C4:NetDisconnect(b, port) NET_LOG[#NET_LOG + 1] = { "disconnect", b, port }; PY_NET("disconnect", b, port, NET_HOSTS[b], nil) end
function C4:SendToNetwork(b, port, data) PY_NET("send", b, port, NET_HOSTS[b], data) end
-- A delivery from the network at NOW_MS + delay: "status" (OnConnectionStatusChanged) or "data" (ReceivedFromNetwork)
function Deliver(delay, kind, b, port, value)
  seq = seq + 1
  DELIVERIES[#DELIVERIES + 1] = { id = seq, due = NOW_MS + delay, kind = kind, b = b, port = port, value = value }
end
-- Run what falls due within `window` ms of the clock (timers and deliveries), earliest first, moving the clock
function Pump(window, maxRuns)
  local limit = NOW_MS + (window or 0)
  local runs = 0
  while runs < (maxRuns or 100000) do
    local best, bi, list
    for i, t in ipairs(TIMERS) do
      if t.active and t.due <= limit and (not best or t.due < best.due or (t.due == best.due and t.id < best.id)) then best, bi, list = t, i, TIMERS end
    end
    for i, d in ipairs(DELIVERIES) do
      if d.due <= limit and (not best or d.due < best.due or (d.due == best.due and d.id < best.id)) then best, bi, list = d, i, DELIVERIES end
    end
    if not best then break end
    if best.due > NOW_MS then NOW_MS = best.due end
    if list == DELIVERIES then
      table.remove(DELIVERIES, bi)
      if best.kind == "status" then OnConnectionStatusChanged(best.b, best.port, best.value)
      else ReceivedFromNetwork(best.b, best.port, best.value) end
    else
      if best.rep then
        seq = seq + 1
        best.id = seq
        best.due = best.due + best.ms
      else
        table.remove(TIMERS, bi)
        best.active = false
      end
      best.fn(best)
    end
    runs = runs + 1
    if #TIMERS > 2000 then
      local keep = {}
      for _, t in ipairs(TIMERS) do if t.active then keep[#keep + 1] = t end end
      TIMERS = keep
    end
  end
  if NOW_MS < limit then NOW_MS = limit end
  return runs
end
function ActiveTimers()
  local out = {}
  for _, t in ipairs(TIMERS) do if t.active then out[#out + 1] = t.ms end end
  return out
end
-- C4:url(): answered by PY_HTTP, at once or (HTTP_ASYNC) when FlushHttp() runs
function C4:url()
  local x = { opts = {} }
  function x:SetOptions(o) self.opts = o; return self end
  function x:OnDone(f) self.done = f; return self end
  local function run(self, method, url, body, headers)
    HTTP_LOG[#HTTP_LOG + 1] = { method = method, url = url, headers = headers, body = body, opts = self.opts, at = NOW_MS }
    local function answer()
      local code, hdrs, rbody = PY_HTTP(method, url, headers, body)
      if code == nil then self.done(self, {}, 7, "Couldn't connect to server") return end
      self.done(self, { { code = code, headers = hdrs, body = rbody } }, 0, nil)
    end
    if HTTP_ASYNC then HTTP_QUEUE[#HTTP_QUEUE + 1] = answer else answer() end
    return self
  end
  function x:Get(url, headers) return run(self, "GET", url, nil, headers) end
  function x:Post(url, body, headers) return run(self, "POST", url, body, headers) end
  return x
end
function FlushHttp()
  local n = 0
  while #HTTP_QUEUE > 0 do
    local q = HTTP_QUEUE; HTTP_QUEUE = {}
    for _, f in ipairs(q) do f(); n = n + 1 end
  end
  return n
end
print = function(s) LOGS[#LOGS + 1] = tostring(s) end
"""


def defaults_from_xml(path):
    xml = open(path, encoding="utf-8").read()
    props = {}
    for block in re.findall(r"<property>(.*?)</property>", xml, re.S):
        name = re.search(r"<name>(.*?)</name>", block).group(1)
        d = re.search(r"<default>(.*?)</default>", block, re.S)
        props[name] = d.group(1) if d else ""
    return props


def py_hash(alg, s, enc):
    data = s.encode("latin-1")
    h = {"MD5": hashlib.md5, "SHA1": hashlib.sha1, "SHA256": hashlib.sha256}[str(alg).upper()](data)
    if str(enc).upper() == "BASE64":
        return base64.b64encode(h.digest()).decode()
    return h.hexdigest().upper()


def lua_bytes(s):
    """A Lua string as it crossed into Python (latin-1 text) -> the bytes it holds."""
    if s is None:
        return b""
    return s if isinstance(s, bytes) else s.encode("latin-1")


def utf8(s):
    """A Lua string holding UTF-8 -> Python text."""
    return lua_bytes(s).decode("utf-8")


class Driver:
    """The driver in a fresh Lua state. props: property values; persist: saved values (a restart)."""

    def __init__(self, props=None, persist=None, responder=None, device_id=900, tz_offset_h=3, now_ms=None):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True, encoding="latin-1")
        g = self.lua.globals()
        self.responder = responder or (lambda m, u, h, b: (None, None, None))
        self.listeners = {}      # (host, port) -> fake with .net(d, op, binding, port, data)
        self.bindings = {}       # binding -> (host, port) of its open connection
        g.PY_HASH = py_hash
        g.PY_HTTP = self._http
        g.PY_NET = self._net
        g.PY_MKTIME = lambda y, mo, d, h, mi, s: calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s))) - tz_offset_h * 3600
        g.START_MS = now_ms or START_MS
        defaults = defaults_from_xml(os.path.join(ROOT, "src", "driver.xml"))
        defaults.update(props or {})
        g.Properties = self.lua.table_from(defaults)
        self.lua.execute(STUB)
        g.DEVICE_ID = device_id
        g.TZ_OFFSET_S = tz_offset_h * 3600
        for k, v in (persist or {}).items():
            g.PERSIST[k] = self._to_lua(v)
        # As the controller reads driver.lua: UTF-8 bytes ("·" in a string is two bytes there too)
        self.lua.execute(build.assemble_lua("test").encode("utf-8"))
        self.g = g

    def _to_lua(self, v):
        if isinstance(v, dict):
            return self.lua.table_from({k: self._to_lua(x) for k, x in v.items()})
        if isinstance(v, list):
            return self.lua.table_from([self._to_lua(x) for x in v])
        return v

    # ---- network
    def listen(self, host, port, fake):
        self.listeners[(host, port)] = fake

    def unlisten(self, host, port):
        self.listeners.pop((host, port), None)

    def _net(self, op, binding, port, host, data):
        key = (host, port)
        if op == "connect":
            self.bindings[binding] = key
        fake = self.listeners.get(key)
        if fake is not None:
            fake.net(self, op, binding, port, lua_bytes(data) if data is not None else None)
        if op == "disconnect":
            self.bindings.pop(binding, None)

    def deliver(self, delay_ms, kind, binding, port, value):
        self.g.Deliver(delay_ms, kind, binding, port, value)

    def net_log(self, op=None):
        out = []
        for e in self.g.NET_LOG.values():
            row = list(e.values())
            if op is None or row[0] == op:
                out.append(row)
        return out

    # ---- HTTP (the picture)
    def _http(self, method, url, headers, body):
        h = dict(headers) if headers is not None else {}
        code, rh, rb = self.responder(method, url, h, body)
        if code is None:
            return None, None, None
        return code, self.lua.table_from(rh or {}), rb

    def async_http(self, on=True):
        self.g.HTTP_ASYNC = on

    def flush_http(self):
        return self.call("FlushHttp")

    def http_log(self):
        return [{"method": e.method, "url": e.url, "at": e.at} for e in self.g.HTTP_LOG.values()]

    # ---- lifecycle
    def start(self, dit="DIT_ADDING", pump_ms=0):
        self.call("OnDriverInit", dit)
        self.call("OnDriverLateInit", dit)
        if pump_ms:
            self.pump(pump_ms)
        return self

    # ---- helpers
    def call(self, fn, *args):
        return self.g[fn](*args)

    def table(self, d):
        return self.lua.table_from(d)

    def run(self, code):
        self.lua.execute(code)

    def eval(self, code):
        return self.lua.eval(code)

    def pump(self, ms=0, max_runs=100000):
        """Run timers and network deliveries due within ms from now (the clock ends at now + ms)."""
        return self.call("Pump", ms, max_runs)

    def now(self):
        return self.g.NOW_MS

    def set_local_time(self, hour, minute=0):
        """Move the clock forward to the next hh:mm local time (the controller's time zone)."""
        tz = self.g.TZ_OFFSET_S
        now_s = self.now() // 1000
        local = datetime.datetime.fromtimestamp(now_s + tz, datetime.timezone.utc).replace(tzinfo=None)
        target = local.replace(hour=hour, minute=minute, second=0)
        if target <= local:
            target += datetime.timedelta(days=1)
        self.pump(int((target - local).total_seconds() * 1000))

    def events(self):
        return list(self.g.EVENTS.values())

    def event_vars(self):
        return [(x[1], utf8(x[2]) if x[2] is not None else None) for x in self.g.EVENT_VARS.values()]

    def clear(self):
        self.lua.execute("EVENTS = {}; HTTP_LOG = {}; EVENT_VARS = {}; VAR_LOG = {}; NET_LOG = {}")
        self.g.LOGS = self.lua.table_from([])

    def var(self, name):
        v = self.g.VARS[name]
        return utf8(v) if isinstance(v, (str, bytes)) else v

    def var_order(self):
        return list(self.g.VAR_ORDER.values())

    def prop(self, name):
        v = self.g.Properties[name]
        return utf8(v) if isinstance(v, (str, bytes)) else v

    def prop_list(self, name):
        v = self.g.PROP_LISTS[name]
        return utf8(v).split(",") if v is not None else None

    def set_prop(self, name, value, pump_ms=2000):
        self.g.Properties[name] = value
        self.call("OnPropertyChanged", name)
        if pump_ms:
            self.pump(pump_ms)

    def action(self, name, pump_ms=0):
        self.call("ExecuteCommand", "LUA_ACTION", self.table({"ACTION": name}))
        if pump_ms:
            self.pump(pump_ms)

    def command(self, name, params=None):
        p = {k: (v.encode("utf-8") if isinstance(v, str) else v) for k, v in (params or {}).items()}
        return self.call("ExecuteCommand", name, self.table(p))

    def from_proxy(self, binding, name, params=None):
        p = {k: (v.encode("utf-8") if isinstance(v, str) else v) for k, v in (params or {}).items()}
        return self.call("ReceivedFromProxy", binding, name, self.table(p))

    def logs(self):
        return [lua_bytes(x).decode("utf-8", "replace") for x in self.g.LOGS.values()]

    def persisted(self):
        def to_py(v):
            if lupa.lua_type(v) == "table":
                return {k: to_py(x) for k, x in v.items()}
            return v
        return {k: to_py(v) for k, v in self.g.PERSIST.items()}

    def timers(self):
        return list(self.g.ActiveTimers().values())


FAILS = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def finish():
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        sys.exit(1)
    print("ALL PASSED")
