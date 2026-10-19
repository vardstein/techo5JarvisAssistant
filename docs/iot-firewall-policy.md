# Jarvis Show v1 — IoT firewall policy

Jarvis Show is designed for a **default-deny** IoT VLAN. A Crown/Checkers device should never need a
blanket `IoT -> LAN` allow rule, and none of its listeners should be exposed to the public internet.

This document describes the deployment boundary expected by the J31–J35 security work. Substitute the
actual Music Assistant, Home Assistant, Dashcast, DNS/NTP and administration addresses used by the
installation. Do not broaden a host-specific rule to an entire trusted subnet merely for convenience.

## Baseline flows

| Direction | Protocol / port | Destination | Required | Purpose / rule |
|---|---|---|---|---|
| HA -> Jarvis Show | TCP 6053 | individual Show | yes | ESPHome/native API. Permit only the Home Assistant host. The provisioned encryption key is still mandatory. |
| Music Assistant -> Jarvis Show | TCP 8928 | individual Show | when Music Assistant is used | Sendspin. Permit only the paired Music Assistant server. `echod` independently rejects every other source IP. |
| Jarvis Show -> Dashcast | TCP 9555 | Dashcast host | when streamed dashboard is used | Authenticated/encrypted Dashcast transport. Never port-forward 9555. |
| Jarvis Show -> HA | TCP, the port in the Show's HA address (443 for HTTPS, often 8123 for plain HTTP) | Home Assistant host | yes | Permit only the configured HA endpoint. Without it the installer's Home Assistant step waits for the Show and gives up, and the dashboard can't log in. |
| Jarvis Show -> Music Assistant | TCP 8095 | Music Assistant host | when Music Assistant is used | Lyrics. Permit only the paired Music Assistant server. |
| Jarvis Show -> Internet | TCP 443 | GitHub (github.com and its release downloads) | yes, until updates come through Home Assistant | Updates. Without it an update fails with "connection refused". Manifests are signed and files hashed, but narrow the rule where the firewall can. |
| Jarvis Show -> mDNS | UDP 5353 | 224.0.0.251, reflected to the HA network | when HA is on another VLAN | Without it Home Assistant and the installer don't find the Show by its `.local` name. |
| Jarvis Show -> router | UDP/TCP 53 | IoT gateway/DNS resolver | yes | DNS. Do not allow arbitrary external DNS when local policy intends to enforce the resolver. |
| Jarvis Show -> router | UDP 123 | IoT gateway/NTP | yes | NTP. The Show asks the servers DHCP names, then pool.ntp.org, then the gateway. A redirect to the router's own NTP covers all three. Without it the clock stays wrong and updates wait. |
| Jarvis Show <-> router | UDP 67/68 | IoT gateway | yes | DHCP. |

## Same-IoT-subnet peer traffic

Native house announcements and intercom use TCP **8181** between Jarvis Show peers. They are separately
authenticated (signed announcements, encrypted/authenticated intercom), but if both devices are on the
same IoT VLAN this traffic normally stays at layer 2 and never reaches the OpenWrt forwarding firewall.

**Do not enable Wi-Fi client isolation / AP isolation for the Jarvis Show SSID unless peer intercom and
announcements are intentionally being disabled or moved through a routed design.** Client isolation would
break same-IoT-subnet Crown/Checkers communication even though the router rules are correct.

Cross-VLAN TCP 8181 is not required for normal operation. The setup/camera/screen diagnostic pages on
8181 are physically gated and should be reachable only from a trusted administration host when needed.

## Optional flows

| Direction | Protocol / port | Destination | Default | Notes |
|---|---|---|---|---|
| admin host -> Jarvis Show | TCP 22 | individual Show | deny | SSH is opt-in and key-only. If enabled, permit one administration host, never the whole LAN/Internet. |
| admin host -> Jarvis Show | TCP 8181 | individual Show | deny | Temporary setup/camera/screen diagnostics. The device also requires its physical-presence setup session. |
| Jarvis Show -> SIP provider | TCP 5061 | configured SIP provider | deny unless configured | SIP signaling is TLS-only. Allow established/related return traffic. |
| Jarvis Show <-> SIP provider | UDP provider-negotiated SRTP | configured SIP provider | deny unless configured | Media is SRTP-only. Prefer provider/IP-specific rules where practical; do not expose a static inbound RTP range from the Internet. |
| Jarvis Show -> Ollama / SearXNG / Wyoming | configured TCP ports | explicitly configured Direct Brain hosts | deny unless Direct Brain is configured | Permit only the configured server IPs and ports. Do not grant general IoT -> server-LAN access. |

Bluetooth pairing is not a routed firewall flow. Jarvis Show keeps pairing in its explicit, temporary
pairing window; it should not be treated as a permanently discoverable management path.

## Explicit denies

At minimum, enforce these concepts after the allows above:

1. **IoT -> trusted LAN: deny** by default.
2. **trusted LAN -> IoT: deny** by default except the explicit HA, Music Assistant and optional admin
   initiators above.
3. **WAN -> Jarvis Show: deny all.** No port forwards for 22, 6053, 8181, 8928 or Dashcast 9555.
4. **Jarvis Show -> arbitrary management services: deny.** There is no runtime need for SMB, database,
   Proxmox, Docker API or other homelab administration ports.
5. Do not create a generic `IoT -> <whole LAN> ACCEPT` rule as a shortcut for Direct Brain.

## OpenWrt rule model

The canonical OpenWrt deployment should express host-specific rules approximately like this (names are
illustrative; use the real zone names and server addresses):

```text
HA host             -> Jarvis Shows       tcp/6053        ACCEPT
Music Assistant     -> Jarvis Shows       tcp/8928        ACCEPT
Admin workstation   -> Jarvis Shows       tcp/22,8181     OPTIONAL ACCEPT
Jarvis Shows        -> Home Assistant     tcp/443 (HA's port) ACCEPT
Jarvis Shows        -> Dashcast           tcp/9555        ACCEPT
Jarvis Shows        -> Music Assistant    tcp/8095        ACCEPT
Jarvis Shows        -> WAN (GitHub)       tcp/443         ACCEPT
Jarvis Shows        -> mDNS reflector     udp/5353        ACCEPT
Jarvis Shows        -> router             dns,ntp,dhcp    ACCEPT
Jarvis Shows        -> configured brain   exact ports     OPTIONAL ACCEPT
Jarvis Shows        -> SIP provider       tcp/5061 + EST  OPTIONAL ACCEPT
IoT zone            -> trusted LAN        any             REJECT
WAN                  -> IoT zone          any             REJECT
```

For installations where the Shows are on their own IoT subnet (say `10.0.9.0/24`) and service hosts are
on the LAN (say `10.0.0.0/24`), write destination-IP-specific exceptions **above** the general IoT-to-LAN reject.
The precise Music Assistant and optional SIP/Direct-Brain endpoints must be filled from the deployment;
do not guess them in firmware or broaden the rule because they are unknown.

## Checking it

The installer checks the outbound rules itself right after first boot: it logs in to the Show and
tries Home Assistant, DashCast, Music Assistant and GitHub from there, looks at whether the clock got
set, and whether it finds the Show by name. For each one that is blocked it prints the rule that is
missing with the Show's address and MAC, the destination, port and protocol filled in. It only reads;
the router is never changed by the installer. The inbound ones (6053, 8928) aren't checked this way,
since the Show doesn't dial them.

## Secure-default acceptance checklist

Before calling a deployment secure:

- [ ] Show devices live on the IoT VLAN/SSID, not the trusted administration LAN.
- [ ] IoT -> trusted LAN is default-deny.
- [ ] No Jarvis/TECHO5 listener is port-forwarded from WAN.
- [ ] HA is the only normal source allowed to device TCP 6053.
- [ ] Music Assistant is the only source allowed to TCP 8928 when Sendspin is enabled.
- [ ] TCP 8181 is not broadly routed; physical-presence setup remains required for private pages.
- [ ] SSH is disabled, or limited to one administration host and key-only authentication.
- [ ] Direct Brain rules name exact hosts/ports rather than the entire server subnet.
- [ ] SIP is disabled unless configured; when configured, signaling is TLS and media is SRTP.
- [ ] Client isolation is **off** when native peer intercom/announcements are expected on the same IoT subnet.
- [ ] `tools/security-regression.sh` passes with the release Go toolchain.
