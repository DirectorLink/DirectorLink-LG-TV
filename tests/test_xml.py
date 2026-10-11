"""driver.xml and documentation checks (offline).   python tests/test_xml.py

Catches definitions Composer rejects at install time (for example a property of type
PASSWORD), and holds the brand rules: the Composer name, maker metadata, no brand on
what the family sees, the two disclaimers word for word in the README (near the top),
the Composer documentation, the change log and every release note, and no long
github.com address of DirectorLink's in anything people read (github.directorlink.io).
"""
import os
import re
import xml.etree.ElementTree as ET

from harness import ROOT, check, finish

PROPERTY_TYPES = {"STRING", "LIST", "RANGED_INTEGER", "RANGED_FLOAT", "LABEL", "DYNAMIC_LIST",
                  "DEVICE_SELECTOR", "COLOR_SELECTOR", "LINK", "SCROLL", "TRACK"}
PARAM_TYPES = {"STRING", "LIST", "RANGED_INTEGER", "RANGED_FLOAT", "DEVICE_SELECTOR", "COLOR_SELECTOR", "CUSTOM_SELECT", "DYNAMIC_LIST"}

DISCLAIMER = "DirectorLink is an independent project, not affiliated with Control4 or Snap One."
LG_DISCLAIMER = "Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner."


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


path = os.path.join(ROOT, "src", "driver.xml")
root = ET.parse(path).getroot()
props = root.findall("config/properties/property")
names = [p.findtext("name") for p in props]
bad = [(p.findtext("name"), p.findtext("type")) for p in props if (p.findtext("type") or "").strip() not in PROPERTY_TYPES]
check(not bad, f"every property type is one Composer supports {bad or ''}")
check(len(names) == len(set(names)), "property names are unique")
lists = [p.findtext("name") for p in props if p.findtext("type") == "LIST" and p.findtext("default") not in [i.text for i in p.findall("items/item")]]
check(not lists, f"every LIST default is one of its items {lists or ''}")
params = [(c.findtext("name"), q.findtext("type")) for c in root.findall("config/commands/command") for q in c.findall("params/param")
          if q.findtext("type") not in PARAM_TYPES]
check(not params, f"every command parameter type is valid {params or ''}")
events = root.findall("events/event")
ids = [e.findtext("id") for e in events]
check(len(ids) == len(set(ids)), "event ids are unique")
check(all((e.findtext("description") or "").strip() for e in events), "every event has a description (Composer's Programming tab needs one)")
for tag in ("properties", "actions", "commands", "script", "documentation"):
    check(root.find("config/" + tag) is not None and root.find(tag) is None, f"<{tag}> is inside <config>")
version = root.findtext("version")
check(version is not None and version.isdigit(), f"<version> is an integer ({version})")
check(root.findtext("minimum_os_version") == "3.3.0", "minimum Control4 OS 3.3.0")
check(root.findtext("control") == "lua_gen" and root.findtext("driver") == "DriverWorks", "a DriverWorks (lua_gen) driver")
check(root.find("config/script").get("file") == "driver.lua", "the script is driver.lua")

# One self-contained device per TV: a combo driver, its own proxy (no tile in the Control4 apps)
check(root.findtext("combo") == "true" and [p.text for p in root.findall("proxies/proxy")] == ["DirectorLink-LG-TV"]
      and root.find("proxies/proxy").get("proxybindingid") is None, "a combo driver, its own proxy (as DirectorLink itself)")
check(root.find("connections") is None, "no connections in driver.xml (the network connections are made at run time)")

# Brand: Composer name and maker metadata; no brand on anything the family sees
check(root.findtext("name") == "DirectorLink · LG TV Notifications", f"Composer name ({root.findtext('name')})")
check(root.findtext("creator") == "DirectorLink" and root.findtext("manufacturer") == "LG"
      and root.findtext("copyright") == "Copyright 2026 DirectorLink", "creator, manufacturer and copyright")
doc = root.find("config/documentation").get("file")
check(doc == "www/documentation.html" and os.path.exists(os.path.join(ROOT, "src", doc)), f"documentation tab file exists ({doc})")
for img in [root.findtext("small"), root.findtext("large")]:
    check(os.path.exists(os.path.join(ROOT, "src", "www", img)), f"icon {img} exists (paths are relative to www/)")
family = [e.findtext("name") for e in events]
check(not [n for n in family if "directorlink" in (n or "").lower()], "no brand in event names")

# What the installer sees, exactly
check(names == ["Status", "TV", "TV IP Address", "TVs Found", "Doorbell", "Camera Alerts", "Doors and Gates", "Alarm", "Other",
                "Quiet Hours", "Log Level"], f"the properties ({names})")
defaults = {p.findtext("name"): p.findtext("default") for p in props}
check([defaults[k] for k in ("Doorbell", "Camera Alerts", "Doors and Gates", "Alarm", "Other")] == ["On", "Off", "Off", "On", "Off"],
      "Doorbell and Alarm on by default, the others off")
check(defaults["Quiet Hours"] in (None, "") and defaults["TV IP Address"] in (None, ""), "Quiet Hours and the address empty by default")
check(all(p.findtext("readonly") == "true" for p in props if p.findtext("name") in ("Status", "TV")), "Status and TV are read-only")
check(not [p for p in props if (p.findtext("type") or "") == "PASSWORD"], "no PASSWORD property type (Composer rejects it)")
actions = [a.findtext("command") for a in root.findall("config/actions/action")]
check(actions == ["FindTVs", "TestNotification", "PairAgain", "PrintDiagnostics"], f"actions ({actions})")
ev = {e.findtext("name"): e.findtext("id") for e in events}
check(ev == {"Notification Shown": "1", "Notification Failed": "2"}, f"events Notification Shown (1) and Notification Failed (2) ({ev})")
cmds = {c.findtext("name"): [q.findtext("name") for q in c.findall("params/param")] for c in root.findall("config/commands/command")}
check(cmds == {"SHOW_NOTIFICATION": ["Title", "Message"]}, f"the command Show Notification (Title, Message) ({cmds})")

# The Lua and driver.xml agree
lua = "".join(read(os.path.join("src", f)) for f in os.listdir(os.path.join(ROOT, "src")) if f.endswith(".lua"))
used = set(re.findall(r'Properties\["([^"]+)"\]', lua)) | set(re.findall(r'KIND_PROPERTY = \{([^}]*)\}', lua)[0].split('"')[1::2])
missing = sorted(used - set(names))
check(not missing, f"every property the driver reads is in driver.xml {missing or ''}")
fired = set(re.findall(r'FireEvent\("([^"]+)"\)', lua))
check(fired == set(ev), f"the driver fires exactly the events in driver.xml ({sorted(fired)})")
lua_actions = set(re.findall(r"^\t(\w+) = \w+,$", re.search(r"ACTIONS = \{(.*?)\n\}", lua, re.S).group(1), re.M))
check(lua_actions == set(actions), f"every action has its function ({sorted(lua_actions)})")

# The disclaimers, word for word; the README has them near the top
notes = ["README.md", "CHANGELOG.md", "NOTICE", "SECURITY.md", os.path.join("src", "www", "documentation.html")] + sorted(
    os.path.join("docs", "releases", f) for f in os.listdir(os.path.join(ROOT, "docs", "releases")) if f.endswith(".md"))
for n in notes:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", read(n)))
    check(DISCLAIMER in text and LG_DISCLAIMER in text, f"{n}: both disclaimers, word for word")
readme = read("README.md")
check(readme.find(DISCLAIMER) < 1200 and readme.find(LG_DISCLAIMER) < 1400, "README: the disclaimers near the top")
check(os.path.exists(os.path.join(ROOT, "docs", "releases", "v" + read("VERSION").strip() + ".md")), "release notes for the version in VERSION")

# Links people see: DirectorLink's GitHub only as github.directorlink.io
for n in notes:
    long_links = re.findall(r"github\.com/DirectorLink[^\s)\"'<]*", read(n), re.I)
    check(not long_links, f"{n}: no long github.com/DirectorLink address {long_links[:2] or ''}")
for n in notes:
    for url in re.findall(r"https?://[^\s)\"'<>]+", read(n)):
        if "directorlink" in url.lower():
            check(url.startswith(("https://directorlink.io", "https://github.directorlink.io")), f"{n}: DirectorLink link {url}")

finish()
