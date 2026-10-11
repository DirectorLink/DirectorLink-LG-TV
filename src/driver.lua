--[[=============================================================================
    DirectorLink · LG TV Notifications for Control4 - DriverWorks driver
    https://directorlink.io/drivers/lg-tv

    Shows notifications as a pop-up on an LG webOS TV, on the home network
    only: no cloud, no LG account, no ThinQ. One driver per TV.

      - The TV: LG's local WebSocket API (webos.lua), paired once with the TV's
        remote; the client key it gives is saved and never logged or shown.
      - What it shows: DirectorLink's notifications (the DirectorLink
        notification agreement, version 1: DIRECTORLINK_NOTIFY), the Show
        Notification command, and the Test Notification action
        (notify.lua): only the kinds switched on, nothing during Quiet Hours,
        at most one pop-up every 5 seconds, a doorbell or the alarm first.
      - Off, in standby, or no answer within 3 seconds: the pop-up is dropped
        with one log line. The driver never turns the TV on.
      - Finding TVs: an SSDP search (ssdp.lua), or the TV's IP address typed in.

    DirectorLink notification agreement v1: variables DIRECTORLINK_NOTIFY = "1"
    and DIRECTORLINK_NOTIFY_KINDS (the kinds switched on, from doorbell,
    camera, door, alarm, other), written at every start and the kinds again at
    every change; the command DIRECTORLINK_NOTIFY (VERSION, ID, KIND, TITLE,
    MESSAGE, PICTURE), by ExecuteCommand or ReceivedFromProxy.

    Copyright 2026 DirectorLink
    SPDX-License-Identifier: Apache-2.0
    DirectorLink is an independent project, not affiliated with Control4 or Snap One.
    Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner.
===============================================================================]]

LOG_PREFIX = "LG TV"
AGREEMENT_VERSION = 1
TEST_TEXT = { title = "Control4", message = "Test: pop-ups from Control4 work on this TV." }

gState = { savedVars = {}, quietOk = true, foundAt = nil, startedAt = nil }

--[[=============================================================================
    Saved state
    DL_TV: the address it applies to, the client key, how the last request to
    pair ended, the port that worked, whether the TV answered at this address,
    and what it said about itself. DL_VARS: LAST_NOTIFICATION.
===============================================================================]]
function SaveState()
	StoreSet("DL_TV", JsonObject({
		host = gTv.host, key = gTv.key, pairing = gTv.pairing, transport = gTv.transport,
		seen = gTv.seenHere and gTv.host or nil,
		info = JsonObject({ model = gTv.info.model, webos = gTv.info.webos }),
	}))
end

local function LoadState()
	local s = StoreGet("DL_TV")
	if type(s) ~= "table" then return end
	local host = JsonField(s, "host")
	local key = JsonField(s, "key")
	if type(key) == "string" and key ~= "" then
		gTv.key = key
		RegisterSecret(key)
	end
	gState.savedHost = type(host) == "string" and host or ""
	local pairing = JsonField(s, "pairing")
	if pairing == "refused" or pairing == "unanswered" then gTv.pairing = pairing end
	local transport = JsonField(s, "transport")
	if transport == "ws" or transport == "wss" then gTv.transport = transport end
	gState.seenHost = JsonField(s, "seen")
	local info = JsonField(s, "info")
	if type(info) == "table" then
		for _, k in ipairs({ "model", "webos" }) do
			local v = JsonField(info, k)
			if type(v) == "string" then gTv.info[k] = v end
		end
	end
end

local function SaveVars()
	StoreSet("DL_VARS", JsonObject({ LAST_NOTIFICATION = GetVar("LAST_NOTIFICATION") or "" }))
end

local function LoadVars()
	local v = StoreGet("DL_VARS")
	if type(v) == "table" then gState.savedVars = v end
end

--[[=============================================================================
    Status and properties
===============================================================================]]
local function SetLogPrefix()
	local name
	pcall(function() name = C4:GetDeviceDisplayName(MyDeviceId()) end)
	LOG_PREFIX = "LG TV" .. ((type(name) == "string" and name ~= "") and (" '" .. name .. "'") or "")
end

function StatusText()
	local text
	if not ValidAddress(gTv.host) then
		text = "Enter the TV's IP address, or pick it in TVs Found"
	elseif gTv.pairing == "refused" then
		text = "Not accepted on the TV: run Pair Again"
	elseif gTv.pairing == "unanswered" then
		text = "Not paired: turn the TV on, then run Pair Again"
	elseif gTv.state == "prompt" then
		text = "Waiting for you to accept on the TV"
	elseif gTv.state == "ready" then
		text = TvIsOn() and "Paired" or "Off"
	elseif not gTv.lastError and (gTv.state == "connecting" or gTv.state == "opening" or gTv.state == "registering" or gTv.state == "idle") then
		text = "Connecting..."
	else
		text = gTv.seenHere and "Off" or "Not reachable"
	end
	if not gState.quietOk then text = text .. " - Quiet Hours not understood: write it as 22:00-07:00" end
	return text
end

-- "OLED55C1PUB · webOS 6.3.0", once the TV said
function TvText()
	local parts = {}
	if gTv.info.model then parts[#parts + 1] = gTv.info.model end
	if TvWebosText() then parts[#parts + 1] = TvWebosText() end
	return table.concat(parts, " · ")
end

function UpdateStatus()
	local status = StatusText()
	if status ~= Properties["Status"] then LogDebug("Status: %s", status) end
	UpdateProperty("Status", status)
	UpdateProperty("TV", TvText())
end

-- From webos.lua: the connection, the pairing or the power changed
function TvChanged()
	local seen = gTv.seenHere and gTv.host or nil
	if seen ~= gState.seenHost then
		gState.seenHost = seen
		SaveState()
	end
	if gTv.info.model ~= gState.savedModel or gTv.info.webos ~= gState.savedWebos then
		gState.savedModel, gState.savedWebos = gTv.info.model, gTv.info.webos
		SaveState()
	end
	UpdateStatus()
end

function TvKeyChanged() SaveState() end
function TvPairingChanged() SaveState() end

function NotifyChanged()
	SaveVars()
end

local function SetKindsVariable()
	SetVar("DIRECTORLINK_NOTIFY_KINDS", KindsText())
end

local function CheckQuietHours()
	local text = Properties["Quiet Hours"] or ""
	local from, to = ParseQuietHours(text)
	gState.quietOk = from ~= false
	if from == false then
		LogWarn("Quiet Hours '%s' not understood: write it as from-to, for example 22:00-07:00 (none until then)", text)
	elseif from then
		local normal = MinutesText(from) .. "-" .. MinutesText(to)
		if normal ~= text then UpdateProperty("Quiet Hours", normal) end
	end
	UpdateStatus()
end

--[[=============================================================================
    The TV's address
===============================================================================]]
local function ApplyAddress(reason)
	local host = trim(Properties["TV IP Address"])
	if host == gTv.host then return end
	if host ~= "" and not ValidAddress(host) then
		LogWarn("TV IP Address '%s' is not an address", host)
		host = ""
	end
	local before = gTv.host
	TvDisconnect("the TV's address changed")
	NotifyFlush("the TV's address changed")
	gTv.host = host
	for _, ws in pairs(gTv.sockets) do ws.host = host end
	-- A new address: the TV there may be asked to pair again, and has not answered yet
	if host ~= gState.savedHost then
		gTv.pairing = "ok"
		gTv.seenHere = false
		gTv.backoff = 0
		gTv.lastError = nil
		gTv.info = {}
		gState.savedHost = host
	end
	SaveState()
	if host ~= "" then
		LogInfo("TV address %s%s", host, before ~= "" and (" (was " .. before .. ")") or "")
		TvConnect(reason or "new address")
	end
	UpdateStatus()
end

--[[=============================================================================
    Finding TVs
===============================================================================]]
local function FoundItem(tv)
	return (tv.name or "LG TV") .. " (" .. tv.ip .. ")"
end

local function FindTVs()
	LogInfo("Looking for LG TVs on the network...")
	SsdpSearch(function(found, err)
		local items = { "-" }
		for _, tv in ipairs(found or {}) do items[#items + 1] = FoundItem(tv) end
		gState.foundAt = NowMs()
		UpdatePropertyList("TVs Found", items, "-")
		if err then return end
		if #items == 1 then
			LogInfo("Find TVs: no LG TV answered. A TV answers while it is on; or type its IP address in TV IP Address")
		else
			local list = {}
			for i = 2, #items do list[#list + 1] = items[i] end
			LogInfo("Find TVs: %s. Pick the TV in TVs Found", table.concat(list, ", "))
		end
	end)
end

local function PickFound()
	local v = Properties["TVs Found"] or "-"
	local ip = string.match(v, "%((%d+%.%d+%.%d+%.%d+)%)$")
	if not ip then return end
	if ip ~= trim(Properties["TV IP Address"]) then
		UpdateProperty("TV IP Address", ip)
		ApplyAddress("picked in TVs Found")
	end
end

--[[=============================================================================
    Notifications in
===============================================================================]]
-- A parameter whatever the case of its name; nil when missing
local function Param(t, name)
	if type(t) ~= "table" then return nil end
	local v = t[name]
	if v == nil then
		local lname = string.lower(name)
		for k, x in pairs(t) do
			if string.lower(tostring(k)) == lname then v = x break end
		end
	end
	if v == nil then return nil end
	return tostring(v)
end

-- DIRECTORLINK_NOTIFY (the DirectorLink notification agreement, version 1)
function DirectorLinkNotify(tParams, via)
	local version = Param(tParams, "VERSION")
	local v = tonumber(version or "1")
	if not v or v < 1 then
		LogWarn("DIRECTORLINK_NOTIFY with VERSION '%s' (this driver reads version 1): ignored", tostring(version))
		return
	end
	local kind = string.lower(trim(Param(tParams, "KIND") or ""))
	if not KIND_PROPERTY[kind] then
		LogDebug("DIRECTORLINK_NOTIFY with KIND '%s': taken as other", kind)
		kind = "other"
	end
	local id = Param(tParams, "ID")
	LogDebug("DIRECTORLINK_NOTIFY (%s): version %s, id %s, kind %s%s", via, tostring(version or "1"), tostring(id), kind,
		Param(tParams, "PICTURE") and ", with a picture" or "")
	NotifyAccept({
		source = "DirectorLink", id = id, kind = kind,
		title = Param(tParams, "TITLE"), message = Param(tParams, "MESSAGE"), picture = Param(tParams, "PICTURE"),
	})
end

local function ShowNotification(tParams)
	NotifyAccept({ source = "programming", title = Param(tParams, "Title"), message = Param(tParams, "Message") })
end

--[[=============================================================================
    Actions
===============================================================================]]
local function TestNotification()
	if not ValidAddress(gTv.host) then
		LogWarn("Test Notification: enter the TV's IP address first")
		return
	end
	LogInfo("Test Notification: sending a pop-up to the TV")
	NotifyAccept({ source = "test", kind = nil, title = TEST_TEXT.title, message = TEST_TEXT.message })
end

local function PairAgain()
	LogInfo("Pair Again: the client key is forgotten; the TV will ask to accept DirectorLink")
	TvDisconnect("Pair Again")
	NotifyFlush("Pair Again")
	if gTv.key then ForgetSecret(gTv.key) end
	gTv.key = nil
	gTv.pairing = "ok"
	gTv.backoff = 0
	gTv.lastError = nil
	SaveState()
	UpdateStatus()
	if ValidAddress(gTv.host) then TvConnect("Pair Again") end
end

local function PrintDiagnostics()
	local v = ControllerVersion()
	local build = ""
	pcall(function() build = tostring(C4:GetDriverConfigInfo("version") or "") end)
	local now = NowMs()
	local lines = {
		"===== DirectorLink · LG TV Notifications - diagnostics =====",
		"Driver        : " .. DRIVER_SEMVER .. (build ~= "" and (" (" .. build .. ")") or "") .. " on Control4 OS " .. (v and v.text or "?")
			.. ", device " .. tostring(MyDeviceId()) .. ", controller " .. tostring(ControllerAddress()),
		"Status        : " .. tostring(Properties["Status"]),
		"TV            : " .. (gTv.host ~= "" and gTv.host or "(no address)") .. " - " .. (TvText() ~= "" and TvText() or "model not known yet"),
		"Pairing       : " .. (gTv.key and "paired (client key saved, never shown)" or "no client key")
			.. (gTv.pairing ~= "ok" and (" - stopped asking: " .. gTv.pairing) or ""),
		"Connection    : " .. gTv.state .. ", port " .. TV_PORTS[gTv.transport] .. " (" .. gTv.transport .. ")"
			.. (gTv.readyAt and (", ready since " .. ClockText(gTv.readyAt)) or "")
			.. (gTv.state == "waiting" and gTv.retryAt and (", next try " .. ClockText(gTv.retryAt)) or "")
			.. ", " .. gTv.attempts .. " attempts",
		"Last error    : " .. (gTv.lastError and (gTv.lastError .. " at " .. ClockText(gTv.lastErrorAt)) or "none"),
		"Power         : " .. (gTv.state == "ready" and TvPowerText() or "not connected"),
		"Kinds on      : " .. (KindsText() ~= "" and KindsText() or "none"),
		"Quiet Hours   : " .. (trim(Properties["Quiet Hours"]) ~= "" and (trim(Properties["Quiet Hours"]) .. (gState.quietOk and (InQuietHours() and " (now)" or " (not now)") or " (NOT UNDERSTOOD)")) or "none")
			.. ", local time " .. os.date("%H:%M"),
		"DirectorLink  : DIRECTORLINK_NOTIFY = " .. tostring(GetVar("DIRECTORLINK_NOTIFY")) .. ", DIRECTORLINK_NOTIFY_KINDS = " .. tostring(GetVar("DIRECTORLINK_NOTIFY_KINDS")),
		"Notifications : " .. gNotify.counts.shown .. " shown, " .. gNotify.counts.failed .. " not shown, " .. gNotify.counts.skipped .. " skipped"
			.. (#gNotify.queue > 0 and (", " .. #gNotify.queue .. " waiting") or "") .. (gNotify.busy and ", one on its way" or ""),
	}
	lines[#lines + 1] = "Last ones     :" .. (#gNotify.history == 0 and " none" or "")
	for _, h in ipairs(gNotify.history) do
		local text = string.gsub(h.text or "", "[\r\n]+", " ")
		if #text > 70 then text = string.sub(text, 1, 70) .. "..." end
		lines[#lines + 1] = "   " .. ClockText(h.at) .. " " .. (h.kind or h.source) .. " '" .. text .. "' - " .. h.result .. (h.why and (" (" .. h.why .. ")") or "")
			.. (h.id and (", id " .. h.id) or "") .. (h.source ~= "DirectorLink" and (", from " .. h.source) or "")
	end
	if gState.foundAt then
		local found = {}
		for _, tv in ipairs(gSsdp.lastFound or {}) do found[#found + 1] = FoundItem(tv) end
		lines[#lines + 1] = "TVs found     : " .. (#found > 0 and table.concat(found, ", ") or "none") .. " (Find TVs at " .. ClockText(gState.foundAt) .. ")"
	end
	lines[#lines + 1] = "Variables     : LAST_NOTIFICATION = '" .. tostring(GetVar("LAST_NOTIFICATION") or "") .. "'"
	lines[#lines + 1] = "==========================================================="
	PrintReport(lines)
end

ACTIONS = {
	FindTVs = FindTVs,
	TestNotification = TestNotification,
	PairAgain = PairAgain,
	PrintDiagnostics = PrintDiagnostics,
}

COMMANDS = {
	SHOW_NOTIFICATION = ShowNotification,
	DIRECTORLINK_NOTIFY = function(tParams) DirectorLinkNotify(tParams, "ExecuteCommand") end,
}

--[[=============================================================================
    DriverWorks entry points
===============================================================================]]
-- Never reorder: Director numbers variables in this order, and DirectorLink reads them.
-- New ones go at the end.
local VARIABLES = {
	{ "LAST_NOTIFICATION", "", "STRING" },
	{ "DIRECTORLINK_NOTIFY", "1", "STRING" },
	{ "DIRECTORLINK_NOTIFY_KINDS", "", "STRING" },
}

function OnDriverInit()
	math.randomseed(os.time())
	gState.startedAt = NowMs()
	LoadState()
	LoadVars()
	local saved = JsonField(gState.savedVars, "LAST_NOTIFICATION")
	for _, v in ipairs(VARIABLES) do
		if v[1] == "LAST_NOTIFICATION" and type(saved) == "string" then v[2] = saved end
		if v[1] == "DIRECTORLINK_NOTIFY_KINDS" then v[2] = KindsText() end
	end
	AddVariables(VARIABLES)
end

function OnDriverLateInit(dit)
	ApplyLogSettings()
	SetLogPrefix()
	-- The agreement: written at every start, so they are right after a driver update too
	SetVar("DIRECTORLINK_NOTIFY", tostring(AGREEMENT_VERSION), true)
	SetVar("DIRECTORLINK_NOTIFY_KINDS", KindsText(), true)
	local host = trim(Properties["TV IP Address"])
	if host ~= "" and not ValidAddress(host) then host = "" end
	gTv.host = host
	if host ~= (gState.savedHost or "") then
		-- The address changed while the driver was not running
		gTv.pairing = "ok"
		gState.seenHost = nil
		gState.savedHost = host
		SaveState()
	end
	gTv.seenHere = host ~= "" and gState.seenHost == host
	local from = ParseQuietHours(Properties["Quiet Hours"])
	gState.quietOk = from ~= false
	UpdatePropertyList("TVs Found", { "-" }, "-")
	UpdateStatus()
	if ValidAddress(host) then
		SetTimer("START", 2000, function() TvConnect("startup") end)
	else
		-- No address yet: look for TVs, so TVs Found is filled when the installer opens the properties
		SetTimer("START", 3000, FindTVs)
	end
end

function OnDriverDestroyed()
	TvDisconnect("the driver stopped")
	KillAllTimers()
end

function OnPropertyChanged(name)
	LogDebug("Property changed: %s = %s", name, tostring(Properties[name]))
	if name == "Log Level" then
		ApplyLogSettings()
	elseif name == "TV IP Address" then
		-- Applied once the installer is done typing
		SetTimer("ADDRESS_APPLY", 1500, function() ApplyAddress() end)
	elseif name == "TVs Found" then
		PickFound()
	elseif name == "Quiet Hours" then
		CheckQuietHours()
	elseif KIND_PROPERTY_NAMES[name] then
		SetKindsVariable()
	end
end

KIND_PROPERTY_NAMES = {}
for _, k in ipairs(NOTIFY_KINDS) do KIND_PROPERTY_NAMES[KIND_PROPERTY[k]] = k end

-- DirectorLink's C4:SendToDevice reaches a driver here or in ExecuteCommand, depending on the device it names
function ReceivedFromProxy(idBinding, strCommand, tParams)
	if strCommand == "DIRECTORLINK_NOTIFY" then
		local ok, err = pcall(DirectorLinkNotify, tParams or {}, "ReceivedFromProxy")
		if not ok then LogError("DIRECTORLINK_NOTIFY failed: %s", err) end
	end
end

function ExecuteCommand(strCommand, tParams)
	tParams = tParams or {}
	if strCommand == "LUA_ACTION" then
		local action = ACTIONS[tParams.ACTION]
		if action then
			LogDebug("Action %s", tostring(tParams.ACTION))
			local ok, err = pcall(action)
			if not ok then LogError("Action %s failed: %s", tostring(tParams.ACTION), err) end
		end
		return
	end
	local cmd = COMMANDS[strCommand]
	if cmd then
		local ok, err = pcall(cmd, tParams)
		if not ok then LogError("Command %s failed: %s", strCommand, err) end
	end
end

function OnConnectionStatusChanged(idBinding, nPort, strStatus)
	if SsdpStatus(idBinding, strStatus) then return end
	WsStatus(idBinding, strStatus)
end

function ReceivedFromNetwork(idBinding, nPort, strData)
	if SsdpData(idBinding, strData) then return end
	WsReceived(idBinding, strData)
end
