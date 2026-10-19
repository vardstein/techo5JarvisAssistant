# Jarvis Show installer reboot/reconnect contract

The installer expects the Show to disappear from USB and enumerate as a different interface several
times. Those transitions are treated as normal state changes, not immediate failures.

Jarvis Show uses polling deadlines rather than fixed sleeps, so fast machines continue immediately
while slow USB hosts still get enough time:

- post-Amonet write and TWRP boot: 900 seconds
- recovery/TWRP enumeration: 300 seconds
- TWRP reboot after userdata format: 300 seconds
- adb -> fastboot enumeration: 180 seconds
- rescue USB serial console: 420 seconds
- first Jarvis rootfs boot + daemon: 600 seconds

A timeout stops at that stage and never falls through to a later write. Fastboot presence probes have
their own 10-second command timeout so a wedged host-side USB process cannot make the outer deadline
infinite.

After `fastbrick.sh` prints "Exploit most likely successful!" it exits, but the Show keeps writing
preloader, LK, TEE and TWRP for up to five minutes and then boots TWRP. During that window Jarvis only
runs `adb devices`, which sends the Show nothing. The unlock counts as proven once exactly one recovery
device passes the TWRP board check and reports the same serial as the locked fastboot device. If TWRP
never shows up, Jarvis asks fastboot once at the end and accepts the same serial with
`unlock_status=true`. Every way out of this stage that isn't success tells the user to keep the Show on
mains power and USB.

These waits do not authorize retrying an unknown destructive state. A failed Amonet/flash stage still
requires the read-only identity/recovery checks before another write-capable stage can run.
