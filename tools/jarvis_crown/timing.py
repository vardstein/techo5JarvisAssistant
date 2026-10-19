"""Installer timing contract for expected Echo Show USB reboot/re-enumeration cycles.

These are deliberately generous wall-clock budgets, not fixed sleeps. Poll loops return immediately
when a stage is ready. The purpose is to survive normal Amonet/TWRP/rescue/first-boot transitions on
slow hosts and USB controllers without hiding a genuinely stuck device forever.
"""

POLL_SECONDS = 3.0
COMMAND_TIMEOUT_SECONDS = 20

# After "Exploit most likely successful!" the payload writes preloader, LK, TEE and TWRP for up to
# five minutes and then boots TWRP. Wait well past that, without sending the device anything.
POST_AMONET_TWRP_TIMEOUT_SECONDS = 900

# Recovery flash/reboot and TWRP's post-format reboot both involve complete USB disconnects.
TWRP_REENUM_TIMEOUT_SECONDS = 300
TWRP_DATA_REBOOT_TIMEOUT_SECONDS = 300

# ADB -> bootloader is normally quick, but some hosts take tens of seconds to rediscover fastboot.
FASTBOOT_REENUM_TIMEOUT_SECONDS = 180

# The rescue initramfs and the first real rootfs boot both bring up a USB serial gadget from scratch.
RESCUE_CONSOLE_TIMEOUT_SECONDS = 420
FIRST_BOOT_TIMEOUT_SECONDS = 600
