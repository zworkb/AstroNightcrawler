---
name: Remote visual companion setup
description: User works remotely - visual companion server must bind to 0.0.0.0 with LAN IP as url-host
type: feedback
---

Always start the visual companion server with `--host 0.0.0.0 --url-host <LAN-IP>` because the user connects remotely from another machine in the same network.

**Why:** The user's Claude Code session runs on a remote machine (192.168.1.51 as of 2026-03-21). Binding to localhost only would make the visual companion unreachable.

**How to apply:** When starting the visual companion, always get the LAN IP via `hostname -I | awk '{print $1}'` and use `--host 0.0.0.0 --url-host <that IP>`.