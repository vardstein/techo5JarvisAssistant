# Known deployment bug closure matrix

This is the closure pass for failures observed while bringing up the original `livingRoomEcho8`.
A v1 release may have an open external/physical acceptance gate, but it must not have an
**unclassified** historical regression.

Status meanings:

- **FIXED** — Jarvis Show source/installer owns the defect and contains the repair/regression gate.
- **OPEN GATE** — product-side work is complete, but real hardware or an external service still has
  to prove the behavior before release acceptance.
- **EXTERNAL** — the behavior is owned by Home Assistant, the network, or a custom HA dashboard; the
  Jarvis firmware must not silently duplicate or override that owner.

| Historical defect | Classification | Closure / release gate |
| --- | --- | --- |
| Show Setup browser told the user to press a physical action button that Crown/Checkers do not have | **FIXED — J36** | Show builds authorize with the real on-screen `Allow` path; browserless form/session regression coverage protects it. |
| Fresh install could reach a state with no saved Wi-Fi and no practical provisioning path | **FIXED — J37** | First boot keeps supplicant/DHCP alive, opens the native picker after no IPv4, keeps Settings -> Wi-Fi available, and exposes USB serial recovery through `jarvis-show wifi`. |
| ESPHome/HA appeared disconnected and zeroconf could not rescue an unprovisioned Show | **FIXED — J37** | Network provisioning is local and USB-recoverable; neither HA nor mDNS/zeroconf is a recovery dependency. |
| Generic TWRP reboot after userdata format could fail the Crown recovery handoff | **FIXED — J21** | Installer uses the proven `twrp reboot recovery` path and waits for `/data` to be mounted and writable before continuing. |
| Host USB serial permissions (`dialout`/equivalent) could break install/recovery halfway through | **FIXED — J17/J25A** | Host preflight and bounded reconnect loops detect access/re-enumeration failures before later destructive stages. |
| Wrong board/newer Echo generation could be selected or cross-flashed | **FIXED — J18/J22B/J23C** | Device product is read first; only `CROWN` and `CHECKERS` are accepted, `CRONOS` is explicitly refused, unknown/newer products fail before writes, and the detected serial remains pinned. |
| Old generic TECHO5 rootfs could accidentally be supplied to the Jarvis installer | **FIXED — J22/J34** | Rootfs hash plus signed `genbu-show-v1` product/board/version identity are mandatory; the archived generic v0.9.21 rootfs is a regression-test rejection case. |
| Wake configuration had multiple assistant/wake slots and could drift into confusing re-arm state | **FIXED — J04/J05/J06** | One product wake slot, deterministic fallback model order, single voice state machine, local Stop, and automatic idle re-arm. |
| HA header disappeared but left a black/reserved strip; sidebar/header ownership was split across TECHO5, Kiosk Mode and custom edge CSS | **OPEN GATE — J38** | Dashcast is now the sole chrome owner, forces full-height HA geometry, zero safe-area/header/sidebar dimensions, board-native viewport and cold generation-aware tabs. A fresh physical Crown and Checkers screenshot/touch acceptance is still mandatory. |
| Old Dashcast warm tabs could resurrect stale dashboard/header geometry | **FIXED — J10** | Warm identity includes UI generation; explicit one-shot cold reload discards the parked matching tab. |
| `jarvis-edge-to-edge.js` / dashboard `kiosk_mode` were competing workarounds | **FIXED — J09/J38** | They are not product dependencies. Dashcast appliance mode owns browser chrome deterministically. |
| WIND separator, weather width, room-card dimensions/text and clock offset were visually tuned in HA and could be disturbed by browser chrome fixes | **EXTERNAL HA UI + J38 gate** | Those cards remain HA-owned. Jarvis Show does not rewrite them; J38 physical acceptance must prove device chrome handling leaves their established geometry unchanged. |
| `Was ist ein Taco?` could be interpreted as a device command, and explicit Taco-recipe web search could become camera/screen control | **OPEN GATE — J39** | Direct Brain firmware now separates information from device actions and has deterministic SearXNG tool-loop regression coverage. The deployed HA/Klar agent still must pass the same two requests while HA owns the turn. |
| Klar's built-in Jarvis personality could add unwanted `Sir` wording | **EXTERNAL HA/Klar configuration** | Personality/tone belongs to the selected HA conversation agent. Jarvis Show must not hard-code a competing personality; use the desired Klar/default prompt configuration server-side. |
| Weather/device behavior failed when the IoT network did not provide usable time/NTP | **EXTERNAL NETWORK POLICY** | Router DNS/NTP/DHCP access is an explicit deployment dependency in `docs/iot-firewall-policy.md`. Firmware must not bypass the segmented-network policy or silently use an unrelated cloud workaround. |
| Setup/debug/network services created avoidable attack surface while recovering the device | **FIXED — J31–J35** | Setup is physical-presence gated, remote ADB removed, Sendspin/source admission hardened, secrets/logs protected, OTA identity signed, and the security regression suite gates releases. |

## Explicitly not closed here

The following are tracked product enhancements rather than forgotten historical defects:

- J41: final upstream anti-brick audit before a real firmware install/release.
- J42–J45: resilient room/group music routing and the final persistent full-screen Music Assistant UX.

J38 and J39 remain release gates. Their open state is intentional and visible; neither may be converted
to PASS from source inspection alone.
