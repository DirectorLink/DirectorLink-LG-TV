# Security policy

## Reporting a vulnerability

Please report security problems privately, not in public issues.

Open this repository's **Security** tab and choose **Report a vulnerability**. This uses GitHub's private vulnerability reporting, so only the maintainers see your report.

Include what you found, the driver version (the *Driver* line of **Print Diagnostics**), and the steps to reproduce it. Leave out real keys, addresses and names.

We will confirm the report, fix the problem in a new release, and credit you if you wish.

## Supported versions

Only the [newest release](../../releases) gets security fixes.

## Scope

The driver runs on the Control4 controller and talks only to the LG TV whose address is in its properties (and, for **Find TVs**, to the LG TVs that answer an SSDP search on the home network, whose description it reads once).

- **The TV's client key** is what the TV gives when someone accepts the driver with the TV's remote. It is sent only to the TV it came from, inside the WebSocket connection. It is never written to a log (every log line passes through a filter that hides it, and a `client-key` field of any message is blanked before the driver even knows the key), a property, a variable or **Print Diagnostics**.
- **The key can do little.** The driver asks the TV only for permission to show pop-ups (`WRITE_NOTIFICATION_TOAST`) and to read whether it is on (`READ_POWER_STATE`). A key that leaked could show pop-ups on that TV; it cannot change channels, inputs, volume, settings or power.
- **Where it is kept.** The key is saved in the driver's persisted data in the Control4 project, in plain form. Director's encrypted persistence did not give a saved value back after a driver update on a real controller (DirectorLink's ADR-028), which would make the TV ask to accept again after every update. The project (and its backups) should be kept as private as the home network.
- **The TV's certificate.** On port 3001 every LG TV shows the same certificate, LG's own, with no address in it, so it cannot be checked against the TV's address: the driver connects without checking it, as every open-source client does, and only ever to the address in its properties. Director cannot pin one certificate. On port 3000, used only when 3001 refuses (TVs before 2018), the connection is not encrypted: someone who can watch the home network's traffic could read the key and the pop-ups' texts there. The key still only shows pop-ups and reads the power state.
- **Who can show pop-ups.** Any driver or programming in the Control4 project can send `DIRECTORLINK_NOTIFY` or **Show Notification**, as it can send any command to any device: the project is trusted.
- **Pairing requests.** The driver asks the TV to pair only after its address is set, when **Pair Again** runs, or when the TV no longer knows its key. After a refusal, or a request nobody answered, it does not ask again until **Pair Again**, so it cannot pester the people watching.

DirectorLink is an independent project, not affiliated with Control4 or Snap One. Not affiliated with or endorsed by LG Electronics. webOS is a trademark of its owner.
