--[[=============================================================================
    DirectorLink LG TV Notifications - the TV (LG webOS, local API)
    LG webOS TVs take commands from apps on the home network over a WebSocket,
    the API LG's own Connect SDK uses and the open-source clients speak
    (Home Assistant's aiowebostv, LGWebOSRemote, lgtv2, openHAB). LG calls it
    SSAP: JSON messages {"type","id","uri","payload"} with ssap:// addresses.

    The connection
      - wss on port 3001 first (TVs with firmware from 2023 take nothing
        else), ws on port 3000 when 3001 refuses or its TLS fails (webOS 1
        and 2, TVs before 2018). The one that worked is tried first next time.
        No answer at all on the first: the TV is not there (off, unplugged),
        and the other is not tried.
      - Every TV has the same certificate on 3001, LG's own, with no address
        in it: no client can check it, every library takes it as it is.
      - Dropped, or never made: tried again after 5 s, 30 s, 2 min, then
        every 5 min; a notification that comes meanwhile tries at once.
      - A ping every 15 s while connected (websocket.lua).

    The opening, as aiowebostv does it: "hello" (with an empty payload,
    which webOS 25 needs), getSystemInfo (the model; newer webOS wants it
    before registering), then "register". Each waits for its answer, briefly.

    Pairing
      - "register" with the permissions the driver asks for (TV_MANIFEST)
        and the client key, once the TV gave one. Without a valid key the TV
        asks the person watching ("pairingType": "PROMPT") and answers
        "registered" with a new key once they accept with the remote, or
        "403 User denied access" when they refuse or let it time out (about
        two minutes). A key the TV no longer knows brings the prompt back.
      - The manifest is not signed. The signed "LG Remote App" manifest the
        libraries copied for years is refused by webOS 26 firmware ("403
        Pairing rejected: blacklisted certificate detected", 2026); LG's
        Connect SDK has always paired without it. Pop-ups and the power state
        need no signature.
      - The driver asks only for what it uses: showing pop-ups and reading
        the power state. The key it gets cannot change channels, inputs,
        settings or power.
      - The key is saved (driver.lua) and registered as a secret: it is never
        logged, shown in a property or in Print Diagnostics.
      - Asked for once: after the TV refused, or nobody answered, the driver
        does not ask again until Pair Again or a new address, so the family
        is not asked over and over.

    On or off
      - The power state, subscribed after registering
        (com.webos.service.tvpower/power/getPowerState). "Active Standby",
        "Suspend", "Power Off" (as aiowebostv counts them) and "Screen Off"
        (nobody would see it) are off, and so is a TV on its way off
        ("processing": "Request Power Off", "Request Active Standby", ...).
        Many TVs keep their network on in standby (Quick Start+): connected
        is not on. An old webOS without that service is on while connected.

    A pop-up
      - ssap://system.notifications/createToast with the text. The TV has
        TV_ANSWER_MS (3 s) to answer; if not connected at that moment, the
        connection gets that long, and the pop-up its own 3 s once made. Off,
        in standby, no answer: the pop-up is dropped. The driver never turns
        the TV on.
      - Never with a picture (iconData): from webOS 4 on many TVs show
        nothing at all, and answer nothing, when a pop-up carries one.

    Copyright 2026 DirectorLink
    SPDX-License-Identifier: Apache-2.0
===============================================================================]]

TV_PORTS = { wss = 3001, ws = 3000 }
TV_BINDINGS = { wss = 6001, ws = 6002 }
TV_BACKOFF_S = { 5, 30, 120, 300 }
TV_ANSWER_MS = 3000           -- the TV answers a pop-up (and a connection made for one) within this
TV_STEP_WAIT_MS = 2000        -- hello and getSystemInfo, before registering
TV_REGISTER_WAIT_MS = 5000    -- the first answer to "register" (registered, the prompt, or an error)
PROMPT_WAIT_MS = 150000       -- the TV's own prompt ends after about two minutes; this is the backstop
TV_POWER_WAIT_MS = 3000       -- the power state, after registering

POWER_URI = "ssap://com.webos.service.tvpower/power/getPowerState"
TOAST_URI = "ssap://system.notifications/createToast"
SYSTEM_INFO_URI = "ssap://system/getSystemInfo"

-- The permissions the driver asks for: pop-ups and the power state, nothing else
TV_PERMISSIONS = { "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE" }

function TvRegisterPayload(key)
	local payload = JsonObject({
		forcePairing = false,
		pairingType = "PROMPT",
		manifest = JsonObject({ appVersion = "1.1", manifestVersion = 1, permissions = TV_PERMISSIONS }),
	})
	if key then payload["client-key"] = key end
	return payload
end

-- Power states that are not on (aiowebostv's, and "Screen Off": nobody would see the pop-up)
local POWER_OFF_STATES = { ["Active Standby"] = true, ["Suspend"] = true, ["Power Off"] = true, ["Screen Off"] = true }

gTv = {
	host = "",
	key = nil,                -- the TV's client key (a secret)
	pairing = "ok",           -- "ok", "refused" (the TV said no, or its prompt ran out), "unanswered" (no answer at all)
	transport = "wss",        -- tried first
	state = "idle",           -- idle, connecting, opening, registering, prompt, ready, waiting, stopped
	power = nil,              -- the TV's power state ("Active", "Active Standby", ...), nil until known
	processing = nil,         -- what the TV is doing ("Request Power Off", ...)
	powerService = nil,       -- false on an old webOS without getPowerState
	seenHere = false,         -- answered at this address before (Off rather than Not reachable)
	backoff = 0,              -- failed attempts in a row
	lastError = nil, lastErrorAt = nil,
	connectedAt = nil, readyAt = nil, retryAt = nil,
	info = {},                -- model (getSystemInfo), webos (hello's deviceOSReleaseVersion)
	pending = {}, seq = 0,
	waiters = {},             -- functions waiting for the TV to be ready (a notification while connecting)
	sockets = {},
	attempts = 0, toasts = 0,
}

local function Now() return NowMs() end

-- TvChanged(), TvKeyChanged(), TvPairingChanged(): driver.lua
local function Changed()
	if TvChanged then TvChanged() end
end

--[[------------------------------------------------------------------ Messages ]]
local function Socket(transport)
	local ws = gTv.sockets[transport]
	if not ws then
		ws = WsNew({
			name = transport, binding = TV_BINDINGS[transport], host = gTv.host, port = TV_PORTS[transport], tls = transport == "wss", path = "/",
			onOpen = function(s) TvOpened(s) end,
			onMessage = function(s, text) TvMessage(s, text) end,
			onClose = function(s, reason, was) TvClosed(s, reason, was) end,
		})
		gTv.sockets[transport] = ws
	end
	ws.host = gTv.host
	return ws
end

local function SendJson(msg)
	local ws = gTv.ws
	if not WsIsOpen(ws) then return false end
	local text = JsonEncode(msg)
	LogTrace("To the TV: %s", string.sub(text, 1, 400) .. (#text > 400 and (" ... (" .. #text .. " bytes)") or ""))
	return WsSend(ws, text)
end

local function NextId(prefix)
	gTv.seq = gTv.seq + 1
	return (prefix or "dl") .. "_" .. gTv.seq
end

-- A request: cb(ok, payload or error text, timedOut) once (a subscription: again at every change)
local function Request(kind, uri, payload, cb, timeoutMs)
	local id = NextId("dl")
	local entry = { cb = cb, uri = uri, subscribe = kind == "subscribe", answered = false }
	gTv.pending[id] = entry
	if timeoutMs then
		SetTimer("TV_REQ_" .. id, timeoutMs, function()
			if gTv.pending[id] == entry and not entry.answered then
				gTv.pending[id] = nil
				pcall(cb, false, "no answer within " .. (timeoutMs / 1000) .. " s", true)
			end
		end)
	end
	if not SendJson({ type = kind, id = id, uri = uri, payload = payload or JsonObject() }) then
		gTv.pending[id] = nil
		KillTimer("TV_REQ_" .. id)
		pcall(cb, false, "not connected")
		return nil
	end
	return id
end

-- The connection is gone: every request still waiting fails (a subscription already answered just ends)
local function FailPending(why)
	local list = gTv.pending
	gTv.pending = {}
	for id, entry in pairs(list) do
		KillTimer("TV_REQ_" .. id)
		if not entry.answered then pcall(entry.cb, false, why) end
	end
end

local function ReleaseWaiters(ok, why)
	local list = gTv.waiters
	gTv.waiters = {}
	for _, fn in ipairs(list) do pcall(fn, ok, why) end
end

local function KillStepTimers()
	KillTimer("TV_STEP")
	KillTimer("TV_REGISTER")
	KillTimer("TV_PROMPT")
	KillTimer("TV_POWER")
end

--[[------------------------------------------------------------------ Connecting ]]
local ScheduleRetry, StopAsking, Register -- forward

local function SetError(why)
	gTv.lastError, gTv.lastErrorAt = why, Now()
end

-- Can the driver connect at all? An address, and the TV did not refuse (or leave unanswered) its
-- last request to pair: then nothing until Pair Again or a new address.
function TvCanConnect()
	return ValidAddress(gTv.host) and gTv.pairing == "ok"
end

local function TryTransport(transport, reason)
	gTv.attempt.tried[transport] = true
	local ws = Socket(transport)
	gTv.ws = ws
	gTv.state = "connecting"
	gTv.attempts = gTv.attempts + 1
	LogDebug("Connecting to the TV at %s (%s, port %d)%s", gTv.host, transport, TV_PORTS[transport], reason and (" - " .. reason) or "")
	WsOpen(ws)
end

function TvConnect(reason)
	KillTimer("TV_RETRY")
	if gTv.state == "connecting" or gTv.state == "opening" or gTv.state == "registering" or gTv.state == "prompt" or gTv.state == "ready" then return end
	if not TvCanConnect() then
		gTv.state = "stopped"
		Changed()
		return
	end
	gTv.attempt = { tried = {}, startedMs = Now() }
	TryTransport(gTv.transport, reason)
	Changed()
end

-- A notification came while not connected: try now rather than at the next retry
function TvConnectNow(reason)
	if gTv.state == "waiting" or gTv.state == "idle" then TvConnect(reason) end
end

-- Closes everything (a new address, Pair Again, the driver going away)
function TvDisconnect(why)
	KillTimer("TV_RETRY")
	KillStepTimers()
	for _, ws in pairs(gTv.sockets) do WsClose(ws, why) end
	gTv.ws = nil
	FailPending(why or "disconnected")
	ReleaseWaiters(false, why or "disconnected")
	gTv.state = "idle"
	gTv.power, gTv.processing = nil, nil
	gTv.readyAt = nil
end

ScheduleRetry = function()
	if not TvCanConnect() then
		gTv.state = "stopped"
		Changed()
		return
	end
	gTv.backoff = gTv.backoff + 1
	local s = TV_BACKOFF_S[math.min(gTv.backoff, #TV_BACKOFF_S)]
	gTv.state = "waiting"
	gTv.retryAt = Now() + s * 1000
	LogDebug("Trying the TV again in %d s", s)
	SetTimer("TV_RETRY", s * 1000, function() TvConnect("retry") end)
	Changed()
end

-- The connection ended: before it opened (try the other port, or wait), or after (wait, then again)
function TvClosed(ws, reason, was)
	if ws ~= gTv.ws then return end
	KillStepTimers()
	if gTv.state == "prompt" then
		-- The TV closed while asking: do not ask again
		return StopAsking("unanswered", "The TV stopped asking to accept DirectorLink (" .. tostring(reason) .. "): run Pair Again with the TV on")
	end
	local wasOpen = was == "open"
	FailPending(reason)
	gTv.power, gTv.processing = nil, nil
	if not wasOpen and gTv.attempt then
		local other = ws.name == "wss" and "ws" or "wss"
		-- Refused, or TLS failed: the other port. No answer at all: the TV is not there.
		if not gTv.attempt.tried[other] and not string.find(reason or "", "timeout", 1, true) then
			LogDebug("The TV did not take the connection on port %d (%s): trying port %d", TV_PORTS[ws.name], reason, TV_PORTS[other])
			return TryTransport(other, "the other port")
		end
	end
	SetError(reason)
	if wasOpen and gTv.readyAt then
		LogInfo("The TV went away (%s)", reason)
	else
		LogDebug("No connection to the TV at %s: %s", gTv.host, reason)
	end
	gTv.readyAt = nil
	ReleaseWaiters(false, reason)
	ScheduleRetry()
end

-- Gives up on this connection and tries again later
local function Abandon(why)
	local ws = gTv.ws
	if ws then WsClose(ws, why) end
	TvClosed(ws, why, "open")
end

--[[------------------------------------------------------------------ The opening: hello, getSystemInfo ]]
local function Step(name, waitMs, nextFn)
	gTv.step = name
	SetTimer("TV_STEP", waitMs, function()
		if gTv.step == name then
			LogDebug("No answer to %s within %d s: going on", name, waitMs / 1000)
			nextFn()
		end
	end)
end

local function AskSystemInfo()
	gTv.step = nil
	gTv.sysinfoId = NextId("sysinfo")
	SendJson({ type = "request", id = gTv.sysinfoId, uri = SYSTEM_INFO_URI, payload = JsonObject() })
	Step("getSystemInfo", TV_STEP_WAIT_MS, function() Register() end)
end

function TvOpened(ws)
	if ws ~= gTv.ws then return end
	gTv.transport = ws.name
	gTv.connectedAt = Now()
	gTv.state = "opening"
	gTv.registered = false
	gTv.registerId, gTv.sysinfoId = nil, nil
	LogDebug("Connected to the TV at %s (%s)", gTv.host, ws.name)
	SendJson({ type = "hello", id = "hello", payload = JsonObject() })
	Step("hello", TV_STEP_WAIT_MS, AskSystemInfo)
	Changed()
end

--[[------------------------------------------------------------------ Pairing ]]
Register = function()
	KillTimer("TV_STEP")
	gTv.step = nil
	gTv.registerId = NextId("register")
	gTv.state = "registering"
	LogDebug("%s", gTv.key and "Registering with the TV's client key" or "Asking the TV to pair")
	SendJson({ type = "register", id = gTv.registerId, payload = TvRegisterPayload(gTv.key) })
	SetTimer("TV_REGISTER", TV_REGISTER_WAIT_MS, function()
		if gTv.state == "registering" and not gTv.registered then
			Abandon("no answer to the request to pair")
		end
	end)
	Changed()
end

-- The TV refused, or nobody accepted: no more asking until Pair Again or a new address
StopAsking = function(how, text)
	gTv.pairing = how
	SetError(text)
	LogWarn("%s", text)
	TvDisconnect(text)
	gTv.state = "stopped"
	if TvPairingChanged then TvPairingChanged() end
	Changed()
end

local function Ready()
	if gTv.state == "ready" then return end
	KillTimer("TV_POWER")
	gTv.state = "ready"
	gTv.readyAt = Now()
	gTv.backoff = 0
	gTv.lastError = nil
	LogDebug("The TV is ready (%s)", TvPowerText())
	Changed()
	ReleaseWaiters(true)
	if NotifyKick then NotifyKick() end
end

local function PowerUpdate(payload)
	local state = JsonField(payload, "state")
	if type(state) ~= "string" then return end
	local before = TvIsOn()
	gTv.power = state
	local processing = JsonField(payload, "processing")
	gTv.processing = (type(processing) == "string" and processing ~= "") and processing or nil
	if gTv.state == "registering" then return Ready() end
	if before ~= TvIsOn() then LogInfo("The TV is now %s", TvPowerText()) end
	Changed()
end

local function AfterRegistered()
	gTv.seenHere = true
	gTv.registered = true
	Request("subscribe", POWER_URI, nil, function(ok, payload)
		if ok then
			gTv.powerService = true
			return PowerUpdate(payload)
		end
		if payload == "not connected" or not WsIsOpen(gTv.ws) then return end
		gTv.powerService = false
		gTv.power, gTv.processing = nil, nil
		LogDebug("The TV does not report its power state (%s): on while connected", tostring(payload))
		if gTv.state == "registering" then Ready() end
	end)
	SetTimer("TV_POWER", TV_POWER_WAIT_MS, function()
		if gTv.state == "registering" then
			LogDebug("No power state from the TV within %d s: taken as on", TV_POWER_WAIT_MS / 1000)
			Ready()
		end
	end)
end

local function OnRegisterMessage(msg)
	local mtype = msg.type
	local payload = JsonField(msg, "payload") or {}
	if mtype == "registered" then
		KillTimer("TV_REGISTER")
		KillTimer("TV_PROMPT")
		local key = JsonField(payload, "client-key")
		if type(key) == "string" and key ~= "" then
			RegisterSecret(key)
			if key ~= gTv.key then
				local first = gTv.key == nil
				if gTv.key then ForgetSecret(gTv.key) end
				gTv.key = key
				LogInfo(first and "Paired: the TV accepted DirectorLink" or "Paired again: the TV gave a new client key")
				if TvKeyChanged then TvKeyChanged() end
			end
		end
		gTv.pairing = "ok"
		gTv.state = "registering" -- until the power state is known
		AfterRegistered()
		Changed()
	elseif mtype == "response" and JsonField(payload, "pairingType") == "PROMPT" then
		KillTimer("TV_REGISTER")
		gTv.state = "prompt"
		LogInfo(gTv.key and "The TV no longer knows this driver's key and asks to accept DirectorLink again: accept it with the TV's remote"
			or "The TV asks to accept DirectorLink: accept it with the TV's remote")
		SetTimer("TV_PROMPT", PROMPT_WAIT_MS, function()
			if gTv.state == "prompt" then
				StopAsking("unanswered", "Nobody accepted DirectorLink on the TV within " .. (PROMPT_WAIT_MS / 1000) .. " s: run Pair Again with the TV on")
			end
		end)
		ReleaseWaiters(false, "the TV is waiting for you to accept DirectorLink")
		Changed()
	elseif mtype == "error" then
		KillTimer("TV_REGISTER")
		KillTimer("TV_PROMPT")
		local err = tostring(JsonField(msg, "error") or "error")
		StopAsking("refused", "The TV did not accept DirectorLink (" .. err .. "): run Pair Again and accept it with the TV's remote")
	end
end

function TvMessage(ws, text)
	if ws ~= gTv.ws then return end
	LogTrace("From the TV: %s", string.sub(text, 1, 600))
	local msg = JsonDecode(text)
	if type(msg) ~= "table" then
		LogDebug("A message from the TV that is not JSON (%d bytes)", #text)
		return
	end
	local id, mtype = JsonField(msg, "id"), JsonField(msg, "type")
	if mtype == "hello" then
		local release = JsonField(JsonField(msg, "payload"), "deviceOSReleaseVersion")
		if type(release) == "string" and release ~= "" then gTv.info.webos = release end
		if gTv.step == "hello" then AskSystemInfo() end
		Changed()
		return
	end
	if id ~= nil and id == gTv.sysinfoId then
		local model = JsonField(JsonField(msg, "payload"), "modelName")
		if mtype == "response" and type(model) == "string" and model ~= "" then gTv.info.model = model end
		if gTv.step == "getSystemInfo" then Register() end
		Changed()
		return
	end
	if id ~= nil and id == gTv.registerId then return OnRegisterMessage(msg) end
	local entry = id and gTv.pending[id]
	if not entry then return end
	KillTimer("TV_REQ_" .. id)
	local first = not entry.answered
	entry.answered = true
	if not entry.subscribe then gTv.pending[id] = nil end
	if mtype == "error" then
		gTv.pending[id] = nil
		pcall(entry.cb, false, tostring(JsonField(msg, "error") or "error"))
	else
		local payload = JsonField(msg, "payload") or {}
		if JsonField(payload, "returnValue") == false then
			pcall(entry.cb, false, tostring(JsonField(payload, "errorText") or JsonField(payload, "errorCode") or "returnValue false"))
		else
			pcall(entry.cb, true, payload, first)
		end
	end
end

-- "webOS 26 (11.2.0)": hello's deviceOSReleaseVersion is LG's own number; from 7 (webOS 22) on, the
-- name people know is that number plus 15 (7 webOS 22, 8 webOS 23 ... 11 webOS 26)
function TvWebosText()
	local v = gTv.info.webos
	if not v then return nil end
	local major = tonumber(string.match(v, "^(%d+)"))
	if major and major >= 7 then return "webOS " .. (major + 15) .. " (" .. v .. ")" end
	return "webOS " .. v
end

--[[------------------------------------------------------------------ On or off ]]
local function GoingOff(processing)
	if not processing then return false end
	return string.find(processing, "Off", 1, true) ~= nil or string.find(processing, "Standby", 1, true) ~= nil
		or string.find(processing, "Suspend", 1, true) ~= nil
end

function TvIsOn()
	if gTv.state ~= "ready" then return false end
	if gTv.power == nil then return true end -- an old webOS without the power service: on while connected
	return not POWER_OFF_STATES[gTv.power] and not GoingOff(gTv.processing)
end

function TvPowerText()
	local p = gTv.power
	if p == nil then return gTv.powerService == false and "on (this webOS does not report its power state)" or "on" end
	local names = {
		["Active"] = "on", ["Active Standby"] = "in standby", ["Suspend"] = "in standby", ["Screen Off"] = "on with its screen off",
		["Screen Saver"] = "showing its screen saver", ["Power Off"] = "off",
	}
	return (names[p] or p) .. (gTv.processing and (" (" .. gTv.processing .. ")") or "")
end

--[[------------------------------------------------------------------ A pop-up ]]
-- done(ok, why) exactly once. item.text; item.sentMs is set when it goes to the TV.
function TvShow(item, done)
	local over = false
	local function finish(ok, why)
		if over then return end
		over = true
		KillTimer("TV_SHOW")
		done(ok, why)
	end
	if not ValidAddress(gTv.host) then return finish(false, "no TV address") end
	if not TvCanConnect() then return finish(false, "not paired with the TV") end
	if gTv.state == "prompt" then return finish(false, "the TV is waiting for you to accept DirectorLink") end

	local function send()
		if over then return end
		if not TvIsOn() then return finish(false, "the TV is " .. TvPowerText()) end
		item.sentMs = Now()
		gTv.toasts = gTv.toasts + 1
		Request("request", TOAST_URI, JsonObject({ message = item.text }), function(ok, res, timedOut)
			if ok then return finish(true) end
			if timedOut then
				finish(false, "no answer from the TV within " .. (TV_ANSWER_MS / 1000) .. " s")
				-- A connection that does not answer is not one (some TVs stop answering pop-ups): make it again
				if gTv.state == "ready" then Abandon("no answer to a pop-up") end
				return
			end
			finish(false, "the TV refused the pop-up: " .. tostring(res))
		end, TV_ANSWER_MS)
	end

	if gTv.state == "ready" then return send() end
	-- Not connected yet: the connection gets TV_ANSWER_MS
	gTv.waiters[#gTv.waiters + 1] = function(ok, why)
		KillTimer("TV_SHOW")
		if ok then send() else finish(false, why) end
	end
	SetTimer("TV_SHOW", TV_ANSWER_MS, function()
		finish(false, "the TV did not answer within " .. (TV_ANSWER_MS / 1000) .. " s (" .. (gTv.seenHere and "off" or "not reachable") .. ")")
	end)
	TvConnectNow("a notification")
end
