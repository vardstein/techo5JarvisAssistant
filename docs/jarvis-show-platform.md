# Jarvis Show v1 platform

Jarvis Show is one shared TECHO5-derived runtime with board-specific product profiles:

| Product | Board | Hardware | Screen |
| --- | --- | --- | --- |
| Jarvis Crown v1 | `crown` / `CROWN` | Echo Show 8 1st gen (C7H6N3) | 1280x800 |
| Jarvis Checkers v1 | `checkers` / `CHECKERS` | Echo Show 5 1st gen (H23K37) | 960x480 |

The daemon, root filesystem, voice behavior, Home Assistant integration, Direct Brain, security hardening and OTA code are shared. The boot image, Amonet payload/TWRP and LineageOS vendor input are board-specific and must never be mixed.

## Safety boundary

A shared rootfs must carry `etc/jarvis-show-release.json` with `product=genbu-show-v1` and an explicit `boards` list. The installer independently proves the fastboot product, Lineage board metadata, recovery board, backup product and boot-image digest.

Crown destructive assets are pinned from the user's known-good archive. Checkers destructive installation remains deliberately blocked until trusted Checkers Amonet/TWRP/boot bytes are locally supplied and SHA-256 pinned. Synthetic tests may exercise the Checkers logic, but they do not authorize a real flash.

## Dashcast

Set `JARVIS_SHOW_BOARD` to `crown`, `checkers` or `cronos`. Appliance mode then owns the viewport and browser chrome:

- Crown: 1280x800
- Checkers: 960x480
- Cronos: 960x480
- route: `/jarvis-display`
- kiosk/headerless/sidebarless: enforced server-side

`JARVIS_CROWN_MODE=1` remains only as a compatibility alias for the first Crown deployment.
