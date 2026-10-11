--[[=============================================================================
    DirectorLink LG TV Notifications - WebSocket client (RFC 6455)
    Runs over a Control4 network connection (C4:CreateNetworkConnection,
    C4:NetPortOptions, C4:NetConnect), TLS ("SSL") or plain TCP, so nothing
    waits on Director's thread: Director calls OnConnectionStatusChanged and
    ReceivedFromNetwork, which the driver passes to WsStatus and WsReceived.
    The same design as DirectorLink's own relay client (driver/src/cloud/
    websocket.lua at https://github.directorlink.io), with what it learned
    on real controllers:
      - Director's own monitoring of the connection is off
        (MONITOR_CONNECTION = false): with it on and no OnPoll answer,
        Director dropped working connections every 10 to 40 minutes. This
        client checks the connection itself, with a ping.
      - Director reports a connection's state with no way to tell one
        connection on a binding from the next. An OFFLINE that answers this
        client's own NetDisconnect, and data or an ONLINE left over from an
        attempt it gave up, are not taken for the connection it makes now.

    The TV's certificate is self-signed (each TV makes its own), so a TLS
    connection checks no certificate (VERIFY_MODE "none"). Director cannot
    pin a single certificate; the connection only ever goes to the address
    in the driver's properties.

    ws = WsNew({ name, binding, host, port, tls, path,
                 onOpen = function(ws) end, onMessage = function(ws, text) end,
                 onClose = function(ws, reason) end })
    WsOpen(ws)  WsSend(ws, text)  WsClose(ws)

    Copyright 2026 DirectorLink
    SPDX-License-Identifier: Apache-2.0
===============================================================================]]

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_CONNECT_TIMEOUT_MS = 8000   -- TCP (and TLS) plus the upgrade answer
WS_PING_MS = 15000             -- a ping this often while open
WS_SILENCE_MS = 40000          -- nothing from the TV this long (two pings unanswered): the connection is gone
WS_MAX_MESSAGE = 1048576

gSockets = {} -- binding -> socket

local PORT_OPTIONS = {
	AUTO_CONNECT = false,
	MONITOR_CONNECTION = false,
	KEEP_CONNECTION = false,
	KEEP_ALIVE = true,
}
local TLS_OPTIONS = {
	AUTO_CONNECT = false,
	MONITOR_CONNECTION = false,
	KEEP_CONNECTION = false,
	KEEP_ALIVE = true,
	VERIFY_MODE = "none", -- the TV's certificate is self-signed
}

-- XOR of two bytes without a bit library (DriverWorks Lua 5.1 does not promise one)
local function XorByte(a, b)
	local r, v = 0, 1
	for _ = 1, 8 do
		if a % 2 ~= b % 2 then r = r + v end
		a, b, v = math.floor(a / 2), math.floor(b / 2), v * 2
	end
	return r
end

local gXorTables = {}
local function XorTable(m)
	local t = gXorTables[m]
	if t then return t end
	t = {}
	for x = 0, 255 do t[x] = string.char(XorByte(x, m)) end
	gXorTables[m] = t
	return t
end

-- Masks (or unmasks) data with a 4-byte key. Large payloads (a picture) go 4 bytes at a time.
function WsMask(data, key)
	local t1, t2, t3, t4 = XorTable(string.byte(key, 1)), XorTable(string.byte(key, 2)), XorTable(string.byte(key, 3)), XorTable(string.byte(key, 4))
	local out, n, i = {}, #data, 1
	while i <= n do
		local a, b, c, d = string.byte(data, i, i + 3)
		if d then
			out[#out + 1] = t1[a] .. t2[b] .. t3[c] .. t4[d]
		else
			out[#out + 1] = t1[a] .. (b and t2[b] or "") .. (c and t3[c] or "")
		end
		i = i + 4
	end
	return table.concat(out)
end

local function BigEndian(value, bytes)
	local b = {}
	for i = bytes, 1, -1 do
		b[i] = string.char(value % 256)
		value = math.floor(value / 256)
	end
	return table.concat(b)
end

-- A client frame: FIN set, masked, with the length encoding RFC 6455 requires
function WsFrame(opcode, payload, key)
	payload = payload or ""
	key = key or RandomBytes(4)
	local len = #payload
	local head
	if len <= 125 then
		head = string.char(0x80 + opcode, 0x80 + len)
	elseif len <= 65535 then
		head = string.char(0x80 + opcode, 0x80 + 126) .. BigEndian(len, 2)
	else
		head = string.char(0x80 + opcode, 0x80 + 127) .. BigEndian(len, 8)
	end
	return head .. key .. WsMask(payload, key)
end

local function Send(ws, data)
	local ok, err = pcall(function() C4:SendToNetwork(ws.binding, ws.port, data) end)
	if not ok then LogDebug("WebSocket %s: send failed: %s", ws.name, err) end
	return ok
end

local function TimerName(ws, what)
	return "WS_" .. ws.name .. "_" .. what
end

-- Asks Director to close the connection. While Director has it up, the OFFLINE that follows is this
-- one's answer, not news about the next connection.
local function Disconnect(ws)
	KillTimer(TimerName(ws, "CONNECT"))
	KillTimer(TimerName(ws, "PING"))
	if ws.linkUp then ws.echo = true end
	pcall(function() C4:NetDisconnect(ws.binding, ws.port) end)
end

-- The connection ended without the driver asking
local function Fail(ws, reason)
	if ws.state == "closed" then return end
	local was = ws.state
	ws.state = "closed"
	ws.buf, ws.fragments = "", nil
	ws.lastReason = reason
	Disconnect(ws)
	LogDebug("WebSocket %s: closed (%s) after %s", ws.name, reason, was)
	if ws.onClose then
		local ok, err = pcall(ws.onClose, ws, reason, was)
		if not ok then LogError("WebSocket %s close handler failed: %s", ws.name, err) end
	end
end

function WsNew(opts)
	local ws = {
		name = opts.name, binding = opts.binding, host = opts.host, port = opts.port, tls = opts.tls and true or false,
		path = opts.path or "/", onOpen = opts.onOpen, onMessage = opts.onMessage, onClose = opts.onClose,
		state = "closed", buf = "", messages = 0, gen = 0, linkUp = false, echo = false,
	}
	gSockets[ws.binding] = ws
	return ws
end

function WsOpen(ws)
	if ws.state ~= "closed" then WsClose(ws) end
	ws.gen = ws.gen + 1
	ws.state = "connecting"
	ws.buf, ws.fragments, ws.fragmentLen = "", nil, 0
	ws.startedMs = NowMs()
	ws.lastData = NowMs()
	if not ValidAddress(ws.host) then return Fail(ws, "no TV address") end
	LogDebug("WebSocket %s: connecting to %s://%s:%d%s", ws.name, ws.tls and "wss" or "ws", ws.host, ws.port, ws.path)
	local ok, err = pcall(function()
		local kind = ws.tls and "SSL" or "TCP"
		if ws.createdHost ~= ws.host then
			C4:CreateNetworkConnection(ws.binding, ws.host, kind)
			ws.createdHost = ws.host
		elseif ws.linkUp and not ws.echo then
			-- Director still has a connection up on this binding that it was never asked to close: it goes first
			Disconnect(ws)
		end
		C4:NetPortOptions(ws.binding, ws.port, kind, ws.tls and TLS_OPTIONS or PORT_OPTIONS)
		C4:NetConnect(ws.binding, ws.port)
	end)
	if not ok then return Fail(ws, "cannot open a connection: " .. tostring(err)) end
	local gen = ws.gen
	SetTimer(TimerName(ws, "CONNECT"), WS_CONNECT_TIMEOUT_MS, function()
		if ws.gen == gen and (ws.state == "connecting" or ws.state == "handshake") then
			Fail(ws, ws.state == "connecting" and "no connection (timeout)" or "no answer to the WebSocket request (timeout)")
		end
	end)
end

-- Closed by the driver: no close handler
function WsClose(ws, reason)
	if ws.state == "closed" then return end
	if ws.state == "open" then Send(ws, WsFrame(8, BigEndian(1000, 2))) end -- normal closure
	ws.state = "closed"
	ws.buf, ws.fragments = "", nil
	ws.lastReason = reason or "closed by the driver"
	ws.gen = ws.gen + 1
	Disconnect(ws)
	LogDebug("WebSocket %s: %s", ws.name, ws.lastReason)
end

function WsSend(ws, text)
	if ws.state ~= "open" then return false end
	return Send(ws, WsFrame(1, text))
end

function WsIsOpen(ws)
	return ws ~= nil and ws.state == "open"
end

local function SendHandshake(ws)
	ws.key = Base64Encode(RandomBytes(16))
	local hostHeader = ws.host .. ":" .. ws.port
	local lines = {
		"GET " .. ws.path .. " HTTP/1.1",
		"Host: " .. hostHeader,
		"Upgrade: websocket",
		"Connection: Upgrade",
		"Sec-WebSocket-Key: " .. ws.key,
		"Sec-WebSocket-Version: 13",
		"User-Agent: " .. UserAgent(),
	}
	ws.state = "handshake"
	Send(ws, table.concat(lines, "\r\n") .. "\r\n\r\n")
end

-- Director: OnConnectionStatusChanged(binding, port, "ONLINE" / "OFFLINE")
function WsStatus(binding, status)
	local ws = gSockets[binding]
	if not ws then return false end
	status = string.upper(tostring(status or ""))
	if status == "ONLINE" then
		ws.linkUp, ws.echo = true, false
		if ws.state == "connecting" then
			LogTrace("WebSocket %s: connected (%d ms), sending the upgrade request", ws.name, NowMs() - ws.startedMs)
			SendHandshake(ws)
		elseif ws.state ~= "handshake" and ws.state ~= "open" then
			-- An attempt given up on came up late: Director must not keep it
			LogTrace("WebSocket %s: closing a connection that came up after it was given up", ws.name)
			Disconnect(ws)
		end
	elseif status == "OFFLINE" then
		local echo = ws.echo
		ws.linkUp, ws.echo = false, false
		if echo then
			LogTrace("WebSocket %s: Director closed the connection it was asked to close", ws.name)
			return true
		end
		if ws.state == "connecting" then
			Fail(ws, "connection refused")
		elseif ws.state == "handshake" or ws.state == "open" then
			Fail(ws, "the connection dropped")
		end
	end
	return true
end

local function ExpectedAccept(key)
	local ok, res = pcall(function() return C4:Hash("SHA1", key .. WS_GUID, { return_encoding = "BASE64", data_encoding = "NONE" }) end)
	return ok and type(res) == "string" and res ~= "" and res or nil
end

local function ParseHandshake(ws)
	local e = string.find(ws.buf, "\r\n\r\n", 1, true)
	if not e then
		if #ws.buf > 16384 then Fail(ws, "the answer to the WebSocket request is too long") end
		return false
	end
	local head = string.sub(ws.buf, 1, e - 1)
	ws.buf = string.sub(ws.buf, e + 4)
	local status = tonumber(string.match(head, "^HTTP/%d%.%d%s+(%d+)") or "")
	local headers = {}
	for k, v in string.gmatch(head, "\r\n([^:\r\n]+):[ \t]*([^\r\n]*)") do headers[string.lower(k)] = trim(v) end
	if status ~= 101 then
		Fail(ws, "the TV answered HTTP " .. tostring(status or "?") .. " to the WebSocket request")
		return false
	end
	if string.lower(headers["upgrade"] or "") ~= "websocket" then
		Fail(ws, "the TV did not switch to WebSocket")
		return false
	end
	local want = ExpectedAccept(ws.key)
	if want and headers["sec-websocket-accept"] ~= want then
		Fail(ws, "the TV's WebSocket answer has a wrong Sec-WebSocket-Accept")
		return false
	end
	KillTimer(TimerName(ws, "CONNECT"))
	ws.state = "open"
	ws.openedMs = NowMs()
	ws.lastReason = nil
	LogDebug("WebSocket %s: open (%d ms)", ws.name, NowMs() - ws.startedMs)
	local gen = ws.gen
	SetTimer(TimerName(ws, "PING"), WS_PING_MS, function()
		if ws.state ~= "open" or ws.gen ~= gen then return end
		if NowMs() - ws.lastData > WS_SILENCE_MS then
			return Fail(ws, "the TV stopped answering (no answer to the ping)")
		end
		Send(ws, WsFrame(9, "dl"))
	end, true)
	if ws.onOpen then
		local ok, err = pcall(ws.onOpen, ws)
		if not ok then LogError("WebSocket %s open handler failed: %s", ws.name, err) end
	end
	return true
end

local function Deliver(ws, text)
	ws.messages = ws.messages + 1
	if ws.onMessage then
		local ok, err = pcall(ws.onMessage, ws, text)
		if not ok then LogError("WebSocket %s message handler failed: %s", ws.name, err) end
	end
end

-- One frame from ws.buf, or false when it is not complete yet
local function ParseFrame(ws)
	local buf = ws.buf
	if #buf < 2 then return false end
	local b1, b2 = string.byte(buf, 1, 2)
	local fin = b1 >= 128
	local opcode = b1 % 16
	local masked = b2 >= 128
	local len = b2 % 128
	local pos = 3
	if len == 126 then
		if #buf < 4 then return false end
		local h, l = string.byte(buf, 3, 4)
		len = h * 256 + l
		pos = 5
	elseif len == 127 then
		if #buf < 10 then return false end
		len = 0
		for i = 3, 10 do len = len * 256 + string.byte(buf, i) end
		pos = 11
	end
	if len > WS_MAX_MESSAGE then
		Fail(ws, "a message from the TV is too large (" .. len .. " bytes)")
		return false
	end
	local key
	if masked then
		if #buf < pos + 3 then return false end
		key = string.sub(buf, pos, pos + 3)
		pos = pos + 4
	end
	if #buf < pos + len - 1 then return false end
	local payload = string.sub(buf, pos, pos + len - 1)
	ws.buf = string.sub(buf, pos + len)
	if key then payload = WsMask(payload, key) end

	if opcode == 1 or opcode == 2 then
		if fin then
			ws.fragments = nil
			Deliver(ws, payload)
		else
			ws.fragments, ws.fragmentLen = { payload }, #payload
		end
	elseif opcode == 0 then
		if not ws.fragments then
			Fail(ws, "the TV sent a continuation frame out of order")
			return false
		end
		ws.fragments[#ws.fragments + 1] = payload
		ws.fragmentLen = ws.fragmentLen + #payload
		if ws.fragmentLen > WS_MAX_MESSAGE then
			Fail(ws, "a message from the TV is too large")
			return false
		end
		if fin then
			local text = table.concat(ws.fragments)
			ws.fragments, ws.fragmentLen = nil, 0
			Deliver(ws, text)
		end
	elseif opcode == 8 then
		local code = #payload >= 2 and (string.byte(payload, 1) * 256 + string.byte(payload, 2)) or nil
		Send(ws, WsFrame(8, string.sub(payload, 1, 2)))
		Fail(ws, "closed by the TV" .. (code and (" (code " .. code .. ")") or ""))
		return false
	elseif opcode == 9 then
		Send(ws, WsFrame(10, payload))
	elseif opcode == 10 then
		ws.lastPong = NowMs()
	end
	return true
end

-- Director: ReceivedFromNetwork(binding, port, data)
function WsReceived(binding, data)
	local ws = gSockets[binding]
	if not ws then return false end
	if ws.state ~= "handshake" and ws.state ~= "open" then
		LogTrace("WebSocket %s: %d bytes from a connection given up on, ignored", ws.name, #tostring(data or ""))
		return true
	end
	ws.lastData = NowMs()
	ws.buf = ws.buf .. tostring(data or "")
	if ws.state == "handshake" and not ParseHandshake(ws) then return true end
	while ws.state == "open" and ParseFrame(ws) do end
	return true
end
