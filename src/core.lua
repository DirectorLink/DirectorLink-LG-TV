--[[=============================================================================
    DirectorLink LG TV Notifications - shared core
    Logging (with secret redaction), utilities, timers, variables and events,
    and the saved settings. tools/build.py puts this file first in the
    packaged driver.lua.

    Copyright 2026 DirectorLink
    SPDX-License-Identifier: Apache-2.0
===============================================================================]]

unpack = unpack or table.unpack

-- Replaced by tools/build.py with the contents of the VERSION file
DRIVER_SEMVER = "dev"

-- "<product>/<version>" (the .c4z file name), in the WebSocket request
USER_AGENT_PRODUCT = "DirectorLink-LG-TV"
function UserAgent()
	return USER_AGENT_PRODUCT .. "/" .. DRIVER_SEMVER
end

--[[------------------------------------------------------------------ Logging
    One "Log Level" property: Off, Errors, Warnings, Info, Debug, Trace.
    Lines go to the Lua tab (print) and to Director's driver log (C4:DebugLog,
    C4:ErrorLog for errors), so a shared driver_log.log shows them.
    Every line passes through Redact(): the TV's client key is registered as
    a secret and never reaches a log, and a "client-key" field of a message
    is blanked before its value is known (the TV's "registered" answer).   ]]
LOG_LEVEL = 3
LOG_PREFIX = "LG TV"
local LEVEL_TAGS = { "ERROR", "WARN", "INFO", "DEBUG", "TRACE" }
LOG_LEVELS = { Off = 0, Errors = 1, Warnings = 2, Info = 3, Debug = 4, Trace = 5 }

local gSecrets = {}

-- A value that must never be logged (shorter values are too likely to be ordinary text)
function RegisterSecret(s)
	if type(s) == "string" and #s >= 4 then gSecrets[s] = true end
end

function ForgetSecret(s)
	if s then gSecrets[s] = nil end
end

local function PlainPattern(s)
	return (string.gsub(s, "[%^%$%(%)%%%.%[%]%*%+%-%?]", "%%%0"))
end

function Redact(msg)
	msg = tostring(msg)
	for s in pairs(gSecrets) do
		if string.find(msg, s, 1, true) then msg = string.gsub(msg, PlainPattern(s), "***") end
	end
	-- A client key the driver does not know yet: {"client-key":"..."} in a message from or to the TV
	if string.find(msg, "client-key", 1, true) or string.find(msg, "client_key", 1, true) then
		msg = string.gsub(msg, '("client[%-_]key"%s*:%s*")[^"]*(")', "%1***%2")
	end
	return msg
end

local function WriteLog(level, line)
	print(line)
	if level <= 1 then
		pcall(function() C4:ErrorLog(line) end)
	else
		pcall(function() C4:DebugLog(line) end)
	end
end

function Log(level, fmt, ...)
	if level > LOG_LEVEL then return end
	local msg
	local n = select("#", ...)
	if n > 0 then
		local args = { ... }
		for i = 1, n do
			if type(args[i]) ~= "number" then args[i] = tostring(args[i]) end
		end
		local ok, res = pcall(string.format, fmt, unpack(args, 1, n))
		msg = ok and res or (tostring(fmt) .. " (format error)")
	else
		msg = tostring(fmt)
	end
	WriteLog(level, Redact(os.date("%H:%M:%S") .. " [" .. (LEVEL_TAGS[level] or "LOG") .. "] " .. LOG_PREFIX .. ": " .. msg))
end

function LogError(...) Log(1, ...) end
function LogWarn(...) Log(2, ...) end
function LogInfo(...) Log(3, ...) end
function LogDebug(...) Log(4, ...) end
function LogTrace(...) Log(5, ...) end

-- Reports (Print Diagnostics) are printed whatever the Log Level
function PrintReport(lines)
	local text = Redact(table.concat(lines, "\n"))
	print(text)
	for line in string.gmatch(text .. "\n", "([^\n]*)\n") do
		pcall(function() C4:DebugLog(LOG_PREFIX .. ": " .. line) end)
	end
end

function ApplyLogSettings()
	LOG_LEVEL = LOG_LEVELS[Properties["Log Level"] or "Info"] or 3
end

--[[------------------------------------------------------------------ Utilities ]]
function trim(s)
	return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

function toboolean(v)
	if type(v) == "boolean" then return v end
	v = string.lower(tostring(v or ""))
	return v == "true" or v == "1" or v == "yes" or v == "on" or v == "enabled"
end

function UpdateProperty(name, value)
	value = tostring(value == nil and "" or value)
	if Properties[name] ~= value then
		pcall(function() C4:UpdateProperty(name, value) end)
		Properties[name] = value
	end
end

-- The items of a LIST property, comma-separated (an item must not hold a comma)
function UpdatePropertyList(name, items, value)
	local clean = {}
	for i, item in ipairs(items) do clean[i] = (string.gsub(tostring(item), ",", " ")) end
	pcall(function() C4:UpdatePropertyList(name, table.concat(clean, ",")) end)
	if value then UpdateProperty(name, value) end
end

function UrlDecode(s)
	s = string.gsub(tostring(s or ""), "%+", " ")
	return (string.gsub(s, "%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end))
end

function Base64Encode(s)
	local ok, res = pcall(function() return C4:Base64Encode(s) end)
	return ok and res or ""
end

-- Milliseconds, for timings and pacing
function NowMs()
	local ok, t = pcall(function() return C4:GetTime() end)
	t = ok and tonumber(t) or nil
	if t and t > 1e12 then return t end
	if t and t > 1e9 then return t * 1000 end
	return os.time() * 1000
end

-- The controller's local time of day, in minutes from midnight (the project's time zone)
function LocalMinutes()
	local t = os.date("*t", math.floor(NowMs() / 1000))
	return t.hour * 60 + t.min
end

-- "21:14:03" local time, for reports
function ClockText(ms)
	return os.date("%H:%M:%S", math.floor((ms or NowMs()) / 1000))
end

function ByteSize(n)
	n = tonumber(n) or 0
	if n >= 1048576 then return string.format("%.1f MB", n / 1048576) end
	if n >= 1024 then return string.format("%.1f KB", n / 1024) end
	return n .. " B"
end

function IsJpeg(body)
	return type(body) == "string" and string.sub(body, 1, 2) == "\255\216"
end

-- Width and height from a JPEG's frame header (nil when not found)
function JpegSize(data)
	if not IsJpeg(data) then return nil end
	local i, n = 3, #data
	while i + 8 <= n do
		if string.byte(data, i) ~= 0xFF then return nil end
		local marker = string.byte(data, i + 1)
		if marker == 0xFF then
			i = i + 1
		else
			if marker >= 0xC0 and marker <= 0xCF and marker ~= 0xC4 and marker ~= 0xC8 and marker ~= 0xCC then
				local h = string.byte(data, i + 5) * 256 + string.byte(data, i + 6)
				local w = string.byte(data, i + 7) * 256 + string.byte(data, i + 8)
				return w, h
			end
			i = i + 2 + string.byte(data, i + 2) * 256 + string.byte(data, i + 3)
		end
	end
	return nil
end

function IsIPv4(s)
	local a, b, c, d = string.match(tostring(s or ""), "^(%d+)%.(%d+)%.(%d+)%.(%d+)$")
	if not a then return false end
	for _, x in ipairs({ a, b, c, d }) do
		if tonumber(x) > 255 then return false end
	end
	return true
end

-- A TV never lives on the controller itself
function ValidAddress(a)
	if type(a) ~= "string" then return false end
	local l = string.lower(trim(a))
	if l == "" or l == "localhost" or l == "0.0.0.0" or l == "::1" or string.match(l, "^127%.") then
		return false
	end
	return string.match(l, "^[%w%.%-]+$") ~= nil
end

function MyDeviceId()
	local ok, id = pcall(function() return C4:GetDeviceID() end)
	return ok and tonumber(id) or nil
end

function ControllerAddress()
	local ok, ip = pcall(function() return C4:GetControllerNetworkAddress() end)
	if ok and type(ip) == "string" and IsIPv4(trim(ip)) then return trim(ip) end
	return nil
end

-- Control4 OS version as { major, minor, patch, text }, or nil when Director does not say
function ControllerVersion()
	local ok, info = pcall(function() return C4:GetVersionInfo() end)
	local text = ok and type(info) == "table" and tostring(info.version or info.VERSION or "") or ""
	local a, b, c = string.match(text, "(%d+)%.(%d+)%.(%d+)")
	if not a then return nil end
	return { tonumber(a), tonumber(b), tonumber(c), text = text }
end

-- Random bytes for the WebSocket key and masks (not secrets: RFC 6455 only asks they be unpredictable
-- to proxies). C4:UUID("Random") where Director has it, math.random otherwise.
function RandomBytes(n)
	local hex = ""
	for _ = 1, 4 do
		if #hex >= n * 2 then break end
		local ok, u = pcall(function() return C4:UUID("Random") end)
		if not ok or type(u) ~= "string" then break end
		hex = hex .. string.gsub(u, "[^%x]", "")
	end
	local out = {}
	for i = 1, n do
		local pair = string.sub(hex, i * 2 - 1, i * 2)
		out[i] = string.char(#pair == 2 and tonumber(pair, 16) or math.random(0, 255))
	end
	return table.concat(out)
end

--[[------------------------------------------------------------------ Saved settings
    Written as "json:" followed by JSON. Director gives a stored JSON text back
    already decoded into a table on some versions, but returns this form as
    written (DirectorLink ADR-028); a table is taken as it comes. Values are
    kept in plain persistence: an encrypted value did not come back after a
    driver update on a real controller, and the TV's client key must survive
    updates, or the TV asks again.                                          ]]
function StoreSet(name, value)
	local ok, err = pcall(function() C4:PersistSetValue(name, "json:" .. JsonEncode(value)) end)
	if not ok then LogError("Saving %s failed: %s", name, err) end
end

function StoreGet(name)
	local ok, v = pcall(function() return C4:PersistGetValue(name) end)
	if not ok or v == nil then return nil end
	if type(v) == "table" then return v end
	if type(v) ~= "string" then return nil end
	if string.sub(v, 1, 5) == "json:" then v = string.sub(v, 6) end
	return (JsonDecode(v))
end

function StoreDelete(name)
	pcall(function() C4:PersistDeleteValue(name) end)
end

--[[------------------------------------------------------------------ Timers ]]
local gTimers = {}

function SetTimer(name, ms, fn, rep)
	if gTimers[name] then
		pcall(function() gTimers[name]:Cancel() end)
		gTimers[name] = nil
	end
	local ok, t = pcall(function()
		return C4:SetTimer(ms, function()
			if not rep then gTimers[name] = nil end
			local ok2, err = pcall(fn)
			if not ok2 then LogError("Timer %s failed: %s", name, err) end
		end, rep and true or false)
	end)
	if ok then gTimers[name] = t else LogError("SetTimer(%s) failed: %s", name, t) end
end

function KillTimer(name)
	if gTimers[name] then
		pcall(function() gTimers[name]:Cancel() end)
		gTimers[name] = nil
	end
end

function TimerActive(name)
	return gTimers[name] ~= nil
end

function KillAllTimers()
	for name in pairs(gTimers) do KillTimer(name) end
end

--[[------------------------------------------------------------------ Variables & events ]]
local gVarValues = {}

function AddVariables(list)
	for _, v in ipairs(list) do
		pcall(function() C4:AddVariable(v[1], v[2], v[3], true, false) end)
		gVarValues[v[1]] = v[2]
	end
end

function SetVar(name, value, force)
	if type(value) == "boolean" then value = value and "1" or "0" end
	value = tostring(value)
	if gVarValues[name] == value and not force then return end
	gVarValues[name] = value
	pcall(function() C4:SetVariable(name, value) end)
end

function GetVar(name)
	return gVarValues[name]
end

function FireEvent(name)
	LogDebug("Event: %s", name)
	local ok, err = pcall(function() C4:FireEvent(name) end)
	if not ok then LogError("FireEvent(%s) failed: %s", name, err) end
end
