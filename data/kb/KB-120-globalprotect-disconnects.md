---
id: KB-120
title: "GlobalProtect VPN disconnects every few minutes"
systems: [GlobalProtect VPN]
symptoms: [disconnects]
audience: employee
status: active
updated: 2026-10-10
---
## Causes, most common first
1. **Client older than 6.2.** Versions 6.0.x and 6.1.x have a keepalive bug: the tunnel drops roughly every 10-15 minutes with reason "keepalive timeout". Fixed in 6.2.3 (approved version).
2. Wi-Fi power saving or a weak home Wi-Fi signal (disconnect reason "network change" or "no network").
3. Phone hotspot or captive-portal networks (hotel, cafe).

## Checks
- VPN session log: the disconnect reason and the client version.
- Device record: installed GlobalProtect version.

## Fix for cause 1 (helpdesk agent may push the update after identity verification)
1. Push GlobalProtect 6.2.3 through Self Service to the employee's managed laptop, or the employee installs it from Self Service > Network > GlobalProtect.
2. The install takes about 3 minutes and needs a VPN reconnect.
3. Confirm the next 30 minutes have no "keepalive timeout" disconnects.

## Fix for cause 2
Turn off "Low Data Mode"/power saving for Wi-Fi, move closer to the router or use Ethernet.

Disconnects that continue on 6.2.3 with a good network: escalate to Network with the session log.
