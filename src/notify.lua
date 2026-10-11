--[[=============================================================================
    DirectorLink LG TV Notifications - notifications
    What reaches the TV, and when:
      - DirectorLink's DIRECTORLINK_NOTIFY (the DirectorLink notification
        agreement, version 1), the Show Notification command, and the Test
        Notification action all come here (NotifyAccept);
      - a repeat of the same ID within 10 minutes is ignored; only the kinds
        switched on in the properties are shown, and nothing during Quiet
        Hours (the test action shows all the same);
      - at most one pop-up every 5 seconds: the others wait their turn, a
        doorbell or the alarm before the rest; a wait too long or a queue too
        full drops the least urgent;
      - the TV side is TvShow(item, done) (driver.lua): it calls done(true)
        once the TV took the pop-up, or done(false, why) (off, not
        reachable, no answer within 3 s, refused);
      - LAST_NOTIFICATION is set, then "Notification Shown" or
        "Notification Failed" fires. One log line for each.
      - A PICTURE is taken and not used: from webOS 4 on, many TVs show
        nothing at all when a pop-up carries a picture (webos.lua).
    Nothing here waits: every step is a timer or a callback.

    Copyright 2026 DirectorLink
    SPDX-License-Identifier: Apache-2.0
===============================================================================]]

NOTIFY_KINDS = { "doorbell", "camera", "door", "alarm", "other" }
KIND_PROPERTY = { doorbell = "Doorbell", camera = "Camera Alerts", door = "Doors and Gates", alarm = "Alarm", other = "Other" }
KIND_LABEL = { doorbell = "Doorbell", camera = "Camera alert", door = "Door or gate", alarm = "Alarm", other = "Other" }
URGENT_KINDS = { doorbell = true, alarm = true }
NOTIFY_GAP_MS = 5000          -- at most one pop-up this often on a TV
DEDUPE_MS = 600000            -- the same ID again within 10 minutes is a repeat
DEDUPE_MAX = 300
QUEUE_MAX = 5                 -- waiting their turn at most
WAIT_MAX_MS = 30000           -- a camera alert, a door, other: dropped after waiting this long
WAIT_MAX_URGENT_MS = 60000    -- a doorbell or the alarm
HISTORY_MAX = 10

gNotify = {
	seen = {}, seenOrder = {},  -- ID -> when it came (ms)
	queue = {},                 -- waiting their turn, in arrival order
	busy = nil,                 -- the one the TV is showing now (waiting for the TV's answer)
	lastSentMs = nil,           -- when the last pop-up went to the TV
	seq = 0,
	history = {},               -- the last HISTORY_MAX, newest first, for Print Diagnostics
	counts = { shown = 0, failed = 0, skipped = 0 },
}

--[[------------------------------------------------------------------ Settings ]]
function KindOn(kind)
	local name = KIND_PROPERTY[kind]
	return name ~= nil and Properties[name] == "On"
end

-- The kinds switched on, in the agreement's order, comma-separated: "doorbell,alarm"
function KindsText()
	local on = {}
	for _, k in ipairs(NOTIFY_KINDS) do
		if KindOn(k) then on[#on + 1] = k end
	end
	return table.concat(on, ",")
end

-- "22:00-07:00" (also "22-7", "22:00 - 07:00", "22.00-07.00", with an en or em dash too): from and to
-- in minutes from midnight.
-- Empty: nil (no quiet hours). Not understood: false.
function ParseQuietHours(text)
	text = trim(text)
	if text == "" then return nil end
	text = string.gsub(text, "\226\128[\147\148]", "-") -- en dash, em dash
	local h1, m1, h2, m2 = string.match(text, "^(%d%d?)[:%.]?(%d?%d?)%s*%-%s*(%d%d?)[:%.]?(%d?%d?)$")
	if not h1 then return false end
	h1, h2 = tonumber(h1), tonumber(h2)
	m1, m2 = tonumber(m1 ~= "" and m1 or "0"), tonumber(m2 ~= "" and m2 or "0")
	if not (m1 and m2) or h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59 then return false end
	local from, to = (h1 % 24) * 60 + m1, (h2 % 24) * 60 + m2
	if from == to then return false end
	return from, to
end

function MinutesText(m)
	return string.format("%02d:%02d", math.floor(m / 60), m % 60)
end

-- Quiet now? From 22:00 to 07:00 takes in midnight; from 13:00 to 15:00 does not. "To" is not part of it.
function InQuietHours(minutes)
	local from, to = ParseQuietHours(Properties["Quiet Hours"])
	if not from then return false end
	minutes = minutes or LocalMinutes()
	if from < to then return minutes >= from and minutes < to end
	return minutes >= from or minutes < to
end

--[[------------------------------------------------------------------ The text
    The TV's pop-up has one text: the title and the message, as given (Hebrew
    and other right-to-left text too), joined by TOAST_JOIN.               ]]
TOAST_JOIN = ": "
TOAST_MAX_BYTES = 1000 -- longer text is cut (at a character boundary)

local function CutUtf8(s, max)
	if #s <= max then return s end
	local i = max
	-- Not inside a UTF-8 character: back up over continuation bytes (10xxxxxx)
	while i > 0 do
		local b = string.byte(s, i + 1)
		if not b or b < 128 or b >= 192 then break end
		i = i - 1
	end
	return string.sub(s, 1, i) .. "..."
end

function ToastText(title, message)
	title, message = trim(title), trim(message)
	local text
	if title ~= "" and message ~= "" then
		text = title .. TOAST_JOIN .. message
	else
		text = title ~= "" and title or message
	end
	return CutUtf8(text, TOAST_MAX_BYTES)
end

-- For a log line: one line, not too long
local function Short(s)
	s = string.gsub(tostring(s or ""), "[\r\n]+", " ")
	return CutUtf8(s, 80)
end

--[[------------------------------------------------------------------ Results ]]
local function Remember(item, result, why)
	table.insert(gNotify.history, 1, {
		at = NowMs(), kind = item.kind, source = item.source, id = item.id, text = item.text, result = result, why = why,
	})
	while #gNotify.history > HISTORY_MAX do table.remove(gNotify.history) end
end

local function Describe(item)
	return (item.kind and KIND_LABEL[item.kind] or "Notification") .. " '" .. Short(item.text) .. "'"
		.. (item.source ~= "DirectorLink" and (" (" .. item.source .. ")") or "")
end

local function Finish(item, ok, why)
	if item.finished then return end
	item.finished = true
	SetVar("LAST_NOTIFICATION", item.text, true)
	if ok then
		gNotify.counts.shown = gNotify.counts.shown + 1
		Remember(item, "shown")
		LogInfo("Shown on the TV: %s", Describe(item))
		FireEvent("Notification Shown")
	else
		gNotify.counts.failed = gNotify.counts.failed + 1
		Remember(item, "not shown", why)
		LogInfo("Not shown (%s): %s", tostring(why), Describe(item))
		FireEvent("Notification Failed")
	end
	if NotifyChanged then NotifyChanged() end
end

local function Skip(item, why)
	gNotify.counts.skipped = gNotify.counts.skipped + 1
	Remember(item, "skipped", why)
	LogDebug("Skipped (%s): %s", why, Describe(item))
end

--[[------------------------------------------------------------------ The queue ]]
local NotifyPump -- forward

local function Urgent(item)
	return URGENT_KINDS[item.kind] == true
end

local function Expired(item, now)
	return now - item.at > (Urgent(item) and WAIT_MAX_URGENT_MS or WAIT_MAX_MS)
end

-- Next to go: the first doorbell or alarm, else the first of the rest
local function NextIndex()
	for i, item in ipairs(gNotify.queue) do
		if Urgent(item) then return i end
	end
	return gNotify.queue[1] and 1 or nil
end

NotifyPump = function()
	if gNotify.busy then return end
	local now = NowMs()
	-- Waited too long: a pop-up about what happened a minute ago helps no one
	local keep = {}
	for _, item in ipairs(gNotify.queue) do
		if Expired(item, now) then Finish(item, false, "waited too long for its turn") else keep[#keep + 1] = item end
	end
	gNotify.queue = keep
	local i = NextIndex()
	if not i then return end
	local item = gNotify.queue[i]
	local wait = gNotify.lastSentMs and (gNotify.lastSentMs + NOTIFY_GAP_MS - now) or 0
	if wait > 0 then
		SetTimer("NOTIFY_NEXT", wait, NotifyPump)
		return
	end
	table.remove(gNotify.queue, i)
	gNotify.busy = item
	item.startedMs = now
	LogDebug("To the TV: %s", Describe(item))
	TvShow(item, function(ok, why)
		if gNotify.busy == item then gNotify.busy = nil end
		if item.sentMs then gNotify.lastSentMs = item.sentMs end
		Finish(item, ok, why)
		NotifyPump()
	end)
end

local function Enqueue(item)
	local q = gNotify.queue
	if #q >= QUEUE_MAX then
		-- Full: the newest one waiting that is not a doorbell or the alarm gives way to the new one.
		-- Only doorbells and alarms waiting: a new doorbell or alarm pushes out the oldest of them,
		-- anything else is dropped.
		local drop
		for j = #q, 1, -1 do
			if not Urgent(q[j]) then drop = j break end
		end
		if not drop and not Urgent(item) then
			return Finish(item, false, "too many notifications at once")
		end
		drop = drop or 1
		local old = table.remove(q, drop)
		Finish(old, false, "too many notifications at once")
	end
	q[#q + 1] = item
	NotifyPump()
end

--[[------------------------------------------------------------------ Entry
    n = { source = "DirectorLink" | "programming" | "test", id, kind, title,
          message, picture }. Returns true when it is on its way to the TV.  ]]
function NotifyAccept(n)
	gNotify.seq = gNotify.seq + 1
	local item = {
		seq = gNotify.seq, source = n.source or "programming", id = n.id, kind = n.kind,
		text = ToastText(n.title, n.message), at = NowMs(),
	}
	if item.text == "" then
		Skip(item, "no title and no message")
		return false
	end
	-- The same ID again (DirectorLink sends each notification once per target; a repeat is the same one)
	if item.id and item.id ~= "" then
		local seenAt = gNotify.seen[item.id]
		if seenAt and item.at - seenAt < DEDUPE_MS then
			Skip(item, "a repeat of " .. item.id)
			return false
		end
		gNotify.seen[item.id] = item.at
		gNotify.seenOrder[#gNotify.seenOrder + 1] = { item.id, item.at }
		while #gNotify.seenOrder > DEDUPE_MAX do
			local old = table.remove(gNotify.seenOrder, 1)
			if gNotify.seen[old[1]] == old[2] then gNotify.seen[old[1]] = nil end
		end
	end
	if item.source == "DirectorLink" and not KindOn(item.kind) then
		Skip(item, KIND_PROPERTY[item.kind] .. " is Off")
		return false
	end
	if item.source ~= "test" and InQuietHours() then
		Skip(item, "Quiet Hours " .. trim(Properties["Quiet Hours"]))
		return false
	end
	if n.picture and n.picture ~= "" then
		LogTrace("Without its picture (pop-ups on the TV never carry one): %s", Describe(item))
	end
	Enqueue(item)
	return true
end

-- Forget what waits (Pair Again, a new address): each fails at once
function NotifyFlush(why)
	local q = gNotify.queue
	gNotify.queue = {}
	for _, item in ipairs(q) do Finish(item, false, why) end
end

-- The TV can take the next one (it just became ready)
function NotifyKick()
	NotifyPump()
end
