# Installer Gate B rehearsal — 2026-10-02

Commit under test: `50f0ccc` (`jarvis show: record live acceptance evidence`)

Result: **PASS for the mocked installer/recovery Gate B rehearsal at this commit.**

This is not hardware acceptance and is not a substitute for rerunning Gate B on the final release commit.
No real Echo was queried, unlocked, flashed, formatted, or otherwise written during this rehearsal. No
Jarvis rootfs/release artifact was built or published.

## Focused result

Two focused Python runs completed successfully:

- 59/59 installer, recovery, Lineage staging, multiboard, reconnect and anti-brick tests PASS.
- 33/33 device-gate, Amonet unlock and unified one-command flow tests PASS.
- Combined Gate B rehearsal: **92/92 PASS**.

The archived TECHO5 Crown boot image and TECHO5 rootfs used by legacy negative/known-good fixture tests
were restored only into ignored `build/` paths. They did not modify Git-tracked source.

## Gate B requirement mapping

| Gate B requirement | Evidence in focused tests |
|---|---|
| Auto-detection selects the correct first-generation profile | `test_generic_identity_accepts_both_and_cross_target_fails`, `test_checkers_uses_same_flow_with_board_pins`, `test_install_show_provisions_board_specific_profile` |
| `CRONOS` is identified as its own board (since GENBU-185; refused when this gate ran), unknown/newer products fail before writes | `test_cronos_second_gen_is_identified`, `test_unknown_product_warns_newer_generation_and_fails_closed`, `test_wrong_product_never_invokes_amonet` |
| Device serial remains pinned through destructive handoffs | `test_auto_detected_serial_cannot_be_swapped_before_flow_identity`, `test_serial_change_after_exploit_fails_closed`, `test_backup_must_match_current_recovery_serial` |
| Already-unlocked devices skip Amonet | `test_already_unlocked_skips_amonet_without_confirmation`, `test_unlocked_crown_one_command_order` |
| Locked devices require board-specific Amonet proof/confirmation | `test_locked_crown_requires_exact_confirmation`, `test_success_runs_known_script_and_reverifies_unlock`, `test_checkers_unlock_is_pin_gated_and_uses_checkers_confirmation` |
| TWRP is board-matched and verified | `test_wrong_twrp_product_fails`, `test_checkers_twrp_flash_needs_explicit_pin_and_only_writes_recovery_swdl`, `test_existing_twrp_avoids_all_fastboot_writes` |
| Complete recovery backup validates before destructive staging | `test_atomic_backup_covers_small_partitions_and_boot_areas`, `test_hash_mismatch_removes_partial_and_never_certifies_backup`, `test_failed_backup_stops_before_plan_and_lineage` |
| Wrong-board Lineage/boot/rootfs assets fail closed | `test_wrong_board_is_visible_before_any_device_write`, `test_modified_boot_is_rejected`, `test_upstream_or_wrong_board_rootfs_is_rejected`, `test_checkers_preflight_accepts_only_checkers_assets` |
| Wi-Fi/Bluetooth ABI checks precede slot-store conversion | `test_driver_gate_requires_wifi_and_bluetooth_exact_kernel_abi`, `test_crown_stage_only_returns_before_release_flash_store`, `test_checkers_stage_only_returns_before_release_flash_store` |
| Final destructive confirmation is exact and board-specific | `test_final_confirmation_is_exact`, `test_bad_final_confirmation_stops_before_first_userdata_write`, Checkers confirmation cases in `test_checkers_install_plan_requires_pinned_boot_and_checkers_confirmation` |
| Expected USB reboot/re-enumeration uses bounded retry windows | all six tests in `tests.test_installer_reconnects` |
| Installer path does not write protected bootloader-chain partitions | `test_jarvis_owned_recovery_never_uses_fastboot_boot`, `test_twrp_handoff_write_targets_are_only_recovery_and_swdl`, `test_normal_product_boot_write_is_boot_partition_only`, `test_ordinary_ota_cannot_write_boot_or_raw_partitions` |

## Residual release gates

Gate B passing does not close the remaining release blockers:

- J38 still requires fresh physical Crown and Checkers display evidence.
- J39 still requires deployed HA/Klar transcript/log evidence.
- The full Go runtime suite still requires the repository-declared Go 1.26.x toolchain; the current sandbox cannot fetch it.
- Gate B must be rerun if tracked installer/recovery/anti-brick source changes after this commit.
- Real-device installation remains prohibited until the complete final acceptance gate is satisfied and explicitly authorized.
