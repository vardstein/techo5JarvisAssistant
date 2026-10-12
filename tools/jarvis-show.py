#!/usr/bin/env python3
"""Jarvis Show v1 installer front-end for Crown and Checkers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jarvis_crown.amonet_upgrade import AdbUpgradeClient, UpgradeError, upgrade_amonet  # noqa: E402
from jarvis_crown.assets import amonet_bundle_dir, asset_path, assets_for, default_cache_dir  # noqa: E402
from jarvis_crown.boot_logo import BootLogoError, SshClient, put_logo  # noqa: E402
from jarvis_crown.boards import profile_for_board, profile_for_product  # noqa: E402
from jarvis_crown.device_gate import DeviceGateError, identify_show  # noqa: E402
from jarvis_crown.flow import FlowError, InstallInputs, run_install_flow  # noqa: E402
from jarvis_crown import genbu  # noqa: E402
from jarvis_crown import home_assistant as ha_api  # noqa: E402
from jarvis_crown import ui  # noqa: E402
from jarvis_crown import music_assistant as ma_api  # noqa: E402
from jarvis_crown import remove as gone  # noqa: E402
from jarvis_crown.shows import known_shows, rename_show, show_named, update_show  # noqa: E402
from jarvis_crown.show_ssh import ShowKeyError, identity_for, key_path, make_key, owner_of, public_key  # noqa: E402
from jarvis_crown.settings import RUN, Asker, Settings, SettingsError, gather, read_secret_file, save_defaults, secret_files  # noqa: E402
from jarvis_crown.preflight import SERIAL_GROUPS, _group_names, preflight_ok, print_checks, run_preflight  # noqa: E402
from jarvis_crown.recovery import RecoveryError, adb_recovery_serials, identify_recovery_show  # noqa: E402
from jarvis_crown.to_fastboot import ToFastbootError, bring_to_fastboot  # noqa: E402
from jarvis_crown.releases import CHANNELS, DEFAULT_CHANNEL, ReleaseError, fetch_rootfs  # noqa: E402
from jarvis_crown.unlock import UnlockError, unlock_show  # noqa: E402

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# How the install flow's stages read as they start.
STAGES = {
    "preflight": "checking this computer and the files",
    "identify": "finding out which Show this is",
    "unlock": "unlocking the bootloader",
    "unlock-skipped": "the bootloader is already unlocked",
    "recovery": "starting TWRP",
    "backup": "backing up the Show as it is",
    "validate-install-inputs": "checking the install files",
    "stage-lineage": "putting LineageOS in place",
    "install": "installing Jarvis",
    "complete": "the install is through",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-show")
    parser.add_argument("command", help="what to do (default preflight); docs/jarvis-crown-installer.md, \"Commands and switches\"", choices=["preflight", "identify", "unlock", "install", "amonet-upgrade", "wifi", "home-assistant", "boot-logo", "remove-show"], nargs="?", default="preflight")
    parser.add_argument("--board", choices=("crown", "checkers"), help="optional first-gen board cross-check; install/identify auto-detect by default")
    parser.add_argument("--amonet-dir", type=Path, help="the unpacked Amonet 2.x package; default ../third_party/<board> or the asset cache")
    parser.add_argument("--amonet-zip", type=Path, help="amonet-upgrade: the pinned Amonet 2.x zip; default from the asset cache")
    parser.add_argument("--amonet-hashes", type=Path, help="trusted JSON SHA-256 map for a board whose Amonet bytes are not built-in")
    parser.add_argument("--twrp-sha256", help="trusted board-specific TWRP SHA-256 when not built-in")
    parser.add_argument("--lineage-zip", type=Path, help="install, amonet-upgrade: the LineageOS 18.1 zip for this board; default: fetched and checked into the asset cache")
    parser.add_argument("--work-dir", type=Path, help="scratch space for the install; default ../work next to the repo")
    parser.add_argument("--backup-dir", type=Path, help="where each unit's backups, key and name are kept; default ../backups")
    parser.add_argument("--name", help="the Show's name, as Home Assistant shows it; install, home-assistant and boot-logo find it by this")
    parser.add_argument("--boot-image", type=Path, help="install: the board's boot image; default: fetched and checked into the asset cache")
    parser.add_argument("--boot-sha256", help="trusted board-specific boot image SHA-256 when not built-in")
    parser.add_argument("--rootfs", type=Path, help="install: a Jarvis root filesystem of your own; default: the --channel release's, signature checked")
    parser.add_argument("--rootfs-sha256", help="install: the SHA-256 of --rootfs")
    parser.add_argument("--channel", choices=CHANNELS, default=DEFAULT_CHANNEL,
                        help="install: the release channel the root filesystem comes from, and the one the Show "
                             "follows for updates afterwards (default %(default)s)")
    parser.add_argument("--wifi", help="install, wifi: the network (SSID) it joins; default: ask, or pick it on the Show's screen")
    parser.add_argument("--wifi-passphrase-file", type=Path, help="a file holding its passphrase; default the keyring, then ask")
    parser.add_argument("--serial", help="the unit's serial: wifi talks to that Show on USB (found when only one is attached); home-assistant takes its key from backups/<serial>")
    parser.add_argument("--ssh-key", type=Path, help="install: an SSH public key root accepts from the first boot, instead of "
                                                     "the one the installer makes for this Show (~/.ssh/genbu_<name>); "
                                                     "refused when it is another Show's")
    parser.add_argument("--no-ssh", action="store_true", help="install: no SSH key, so SSH stays off")
    setup = parser.add_argument_group("what the Show is ready with (asked for when left out)")
    setup.add_argument("--ha-url", help="Home Assistant's address as the Show reaches it")
    setup.add_argument("--ha-token-file", type=Path,
                       help="a file holding a long-lived Home Assistant token made by hand for the Show; without "
                            "it the Show gets a user of its own when an admin adds it")
    setup.add_argument("--ha-admin-token-file", type=Path,
                       help="a file holding an admin token, used for this run instead of signing in as an admin")
    setup.add_argument("--own-ha-user", action="store_true",
                       help="insist on the Show getting a Home Assistant user of its own (not an admin), which "
                            "it does anyway when there is an admin; fails without one")
    setup.add_argument("--dashcast", help="the DashCast server, host[:port]")
    setup.add_argument("--dashcast-key-file", type=Path, help="a file holding the DashCast key; default the keyring, then ask")
    setup.add_argument("--music-assistant", help="the Music Assistant server's IP address or name")
    setup.add_argument("--root-password-file", type=Path,
                       help="install: a file holding root's password for the USB console; default the keyring, then ask")
    setup.add_argument("--music-assistant-local-metadata", action="store_true",
                       help="switch Music Assistant's online metadata lookups off, for a library that is already "
                            "tagged; off unless given")
    setup.add_argument("--music-assistant-token-file", type=Path,
                       help="a file holding a Music Assistant admin's token, used once to give the Show a "
                            "user of its own there (for lyrics) and kept nowhere; default the keyring, then ask")
    setup.add_argument("--wake-word", help="the wake word, one the Show offers (default: ask; '' leaves it alone)")
    setup.add_argument("--assistant", help="the Assist pipeline it talks to, by name (default: ask; '' leaves it alone)")
    setup.add_argument("--room", help="the Home Assistant area it stands in, by name (default: ask; '' leaves it alone)")
    setup.add_argument("--room-dashboard", action=argparse.BooleanOptionalAction, default=None,
                       help="make a small dashboard for its room when the house has none, the page a swipe in "
                            "from the left goes on to (default: ask; Enter is no)")
    setup.add_argument("--voice-extras", action=argparse.BooleanOptionalAction, default=None,
                       help="set up the Home Assistant pieces around the Shows: the room going quiet while a "
                            "Show listens, and with a German assistant local answers (room, temperature, "
                            "forecast, volume) and music by voice (default: ask; Enter is no)")
    setup.add_argument("--rename-entities", action=argparse.BooleanOptionalAction, default=None,
                       help="for a Show renamed since it was installed: give its entities the new name too, "
                            "and the automations and dashboards that use them (each saved first) "
                            "(default: ask; Enter is no)")
    setup.add_argument("--calendars",
                       help="the Home Assistant calendars the Show shows, like 'calendar.personal, calendar.family' "
                            "(default: ask; '' leaves them alone)")
    setup.add_argument("--caldav-url", help="add this CalDAV calendar account to Home Assistant (Nextcloud, iCloud, "
                                            "mailbox.org ...), with --caldav-user and --caldav-password-file")
    setup.add_argument("--caldav-user", help="its user name")
    setup.add_argument("--caldav-password-file", type=Path,
                       help="a file holding its password, best an app password; it goes to Home Assistant and "
                            "is kept nowhere here")
    role = setup.add_mutually_exclusive_group()
    role.add_argument("--main-update-show", dest="update_role", action="store_const", const="main",
                      help="make it a main update Show, the one that looks for and asks about updates "
                           "(default: ask; Enter is yes for the first Show, follow for the next)")
    role.add_argument("--follow", dest="update_role", action="store_const", const="follow",
                      help="have it follow the house's main update Show")
    setup.add_argument("--no-questions", action="store_true", help="ask nothing; take switches and the keyring only")
    hass = parser.add_argument_group("home-assistant: add an installed Show to Home Assistant")
    hass.add_argument("--host", help="the Show's address, when Home Assistant has not discovered it; install and "
                                     "unlock reboot a Show running Jarvis there into fastboot over SSH")
    hass.add_argument("--ssh-identity", type=Path, help="the private key that logs in to the Show over SSH "
                                                        "(default ~/.ssh/genbu_<name> when it is there)")
    hass.add_argument("--key-file", type=Path, help="its encryption key (default backups/<serial>/home-assistant.key)")
    gone = parser.add_argument_group("remove-show: take a Show out of Home Assistant and Music Assistant (--name)")
    gone.add_argument("--yes-remove", action="store_true",
                      help="remove without typing the Show's name first; without it, --no-questions only lists")
    logo = parser.add_argument_group("boot-logo: the OpenJade logo at boot on an installed Show (--name, --host)")
    logo.add_argument("--amazon-logo", action="store_true", help="put Amazon's logo back instead")
    return parser.parse_args()


def _show_ssh_key(args) -> Path | None:
    """The public key the Show lets root in with: the one given, else a pair made for this Show alone.
    A key that already opens another Show is refused, so one key never opens two."""
    if args.no_ssh:
        ui.out.note("SSH stays off (--no-ssh); the USB console is the way in.", indent=2)
        return None
    if args.ssh_key:
        pub = args.ssh_key.expanduser().resolve()
        other = owner_of(public_key(pub), args.name)
        if other:
            raise ShowKeyError(f"{args.ssh_key} is the key of the Show {other!r}; each Show gets a key of its own")
        return pub
    ui.out.heading("SSH key for this Show")
    path = key_path(args.name)
    ui.out.note(f"The installer makes an SSH key for {args.name} and nothing else: {_home(path)}. "
                "Only this Show lets it in, and it is never used for another one.", indent=2)
    path, made = make_key(args.name)
    ui.out.field("key", f"{_home(path)} ({'made now' if made else 'already there from an earlier install, kept'})", pad=16)
    ui.out.note(f"Log in later with: ssh -i {_home(path)} root@<the Show's address>", indent=2)
    return path.with_name(path.name + ".pub")


def _home(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + "/") else str(path)


def load_hash_manifest(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Amonet hash manifest: {exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Amonet hash manifest must be a non-empty JSON object")
    result: dict[str, str] = {}
    for name, digest in payload.items():
        if not isinstance(name, str) or not name or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest.lower()):
            raise ValueError(f"invalid Amonet hash manifest entry for {name!r}")
        result[name] = digest.lower()
    return result


def _validate_optional_sha(label: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be exactly 64 hexadecimal characters")
    return normalized


def _print_identity(identity, profile, source: str) -> None:
    ui.out.title(f"{profile.model}", f"{identity.product} · serial ending {identity.serial[-4:]} · seen over {source}")
    ui.out.field("bootloader", "unlocked" if identity.unlocked else "locked")


def _detect_profile(board: str | None, *, allow_recovery: bool = False):
    try:
        identity = identify_show(expected_board=board)
        source = "fastboot"
    except DeviceGateError as fastboot_error:
        if not allow_recovery or board is None:
            raise
        try:
            identity = identify_recovery_show(board)
        except RecoveryError as recovery_error:
            raise DeviceGateError(
                f"fastboot identity unavailable ({fastboot_error}); "
                f"recovery identity unavailable ({recovery_error})"
            ) from recovery_error
        source = "recovery"
    profile = profile_for_product(identity.product)
    _print_identity(identity, profile, source)
    return identity, profile, source


def _cached(board: str, kind: str) -> Path:
    asset = next(a for a in assets_for(board) if a.kind == kind)
    return asset_path(default_cache_dir(), board, asset)


class InstallFilesError(RuntimeError):
    pass


def _load_fetcher():
    import importlib.util
    path = Path(__file__).resolve().parent / "fetch-show-assets.py"
    spec = importlib.util.spec_from_file_location("jarvis_fetch_show_assets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_files(args, board: str, amonet: Path, work: Path, *, fetcher=_load_fetcher,
                   rootfs_from=fetch_rootfs) -> dict:
    """The LineageOS zip, boot image and Amonet folder (passed in, else fetch-show-assets for this board)
    and the root filesystem (passed in, else the --channel release's)."""
    import techo5lib
    files: dict = {"lineage": args.lineage_zip, "boot": args.boot_image, "amonet_dir": amonet}
    if not (args.lineage_zip and args.boot_image and amonet.is_dir()):
        fetch = fetcher()
        try:
            got = fetch.fetch_board(board, default_cache_dir(), browser=fetch.wait_for_browser)
        except techo5lib.Fail as exc:
            raise InstallFilesError(f"the {board} install files: {exc}") from None
        files["lineage"] = args.lineage_zip or got["lineage"]
        files["boot"] = args.boot_image or got["boot"]
        if not args.amonet_dir and "amonet_dir" in got:
            files["amonet_dir"] = Path(got["amonet_dir"]).resolve()
        if not files["amonet_dir"].is_dir():
            raise InstallFilesError(f"no Amonet package for {board}; fetch-show-assets.py --board {board} "
                                    "says where to download it")
    if not args.rootfs:
        rel = rootfs_from(args.channel, board, work)
        ui.out.done(f"Jarvis Show {rel.version} from the {rel.channel} channel, signature checked")
        files["rootfs"], files["rootfs_sha256"] = rel.path, rel.sha256
    return files


def _asker(args) -> Asker:
    return Asker(interactive=not args.no_questions and not os.environ.get("TECHO5_NO_PROMPT")
                 and sys.stdin.isatty() and sys.stdout.isatty())


def _find_host(name: str):
    """The Show's address over mDNS, for when Home Assistant has not discovered it on its own."""
    tool = shutil.which("avahi-resolve-host-name")

    def find():
        if not tool:
            return None
        try:
            out = subprocess.run([tool, "-4", ha_api.node_slug(name) + ".local"], capture_output=True,
                                 text=True, timeout=8, check=False).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out[1] if len(out) == 2 else None
    return find


# How the assistant, wake word, room and room dashboard questions read, by the label deploy() asks them with.
QUESTIONS = {  # label: (question, help, names for the options, the answers Enter takes when offered)
    "assistant": ("Which assistant should the Show talk to?", "",
                  {"preferred": "the one Home Assistant has as preferred"}, ("jarvis",)),
    "wake word": ("Which wake word should it listen for?", "",
                  {"no_wake_word": "none, no wake word"}, ("okay nabu", "nabu")),
    "room": ("Which room is it in?", "It plays and shows that room's music.", {}, ()),
    "room dashboard": ("The room has no dashboard of its own. Make a small one?",
                       "Heating, temperature, lights and blinds; the Show swipes on to it.", {}, ()),
    "voice extras": ("Set up the voice extras?",
                     "The room's music goes quiet while the Show listens. With a German assistant it also "
                     "answers about the room, the temperature, the weather and the volume, and plays music by "
                     "voice. Anything you made yourself is only replaced if you say so.", {}, ()),
    "new name": ("The Show has a new name. Give its entities the new name too?",
                 "Its automations, scripts and dashboards follow, each saved first. The history stays.", {}, ()),
    "calendar account": ("Add a calendar account to Home Assistant?",
                         "For the dashboard's calendar card and the Show's own calendar page. One it has already is "
                         "replaced, never added twice. Home Assistant "
                         "keeps the password; the installer and the Show don't.", {}, ()),
    "genbu missing": ("Genbu is not in Home Assistant yet. Check again?",
                      "Copy ha/custom_components/genbu/ to /config/custom_components/genbu/ on Home Assistant, "
                      "restart it, then pick again. Skipped, the Show looks for updates by itself.",
                      {"again": "again, it is copied and Home Assistant restarted", "skip": "skip"}, ("skip",)),
    "main update show": ("Make this Show the main update Show for your Shows?",
                         "It will be the only one that looks for updates and asks about them; the others follow "
                         "it. Recommended for your first Show.", {}, ("yes",)),
    "follow main": ("How should this Show get its updates?",
                    "The main update Show looks for updates and asks about them; the others follow it.",
                    {}, ("follow",)),
    "second main": ("Make this one a main update Show as well?",
                    "With two, the other Shows follow whichever of them is newer. They cannot fight, but you get "
                    "two Shows that ask about updates.", {"no": "no, it follows"}, ("no",)),
    "calendars": ("Which calendars should the Show show?",
                  "On its calendar page, a tap on the date under the clock. More can be picked later on the "
                  "Show: Settings, General, Calendars.", {}, ("all", "calendar.")),
}


def _chooser(args):
    """Asks for the assistant, wake word and room the switches left open; '' on a switch means do not touch it.
    Enter takes Jarvis when this Home Assistant and this Show have it."""
    asker = _asker(args)
    if not asker.interactive:
        return None
    given = {"assistant": args.assistant, "wake word": args.wake_word, "room": args.room,
             "room dashboard": args.room_dashboard, "voice extras": args.voice_extras,
             "calendars": args.calendars, "new name": getattr(args, "rename_entities", None)}

    def choose(label: str, current: str, options: list[str], shown: dict[str, str] | None = None) -> str | None:
        if given.get(label) is not None:
            return None
        question, help, names, liked = QUESTIONS.get(label, (label, "", {}, ()))
        names = {**names, **(shown or {})}
        default = next((o for want in liked for o in options if o.casefold() == want), None) or \
            next((o for o in options if any(want in o.casefold() for want in liked)), None)
        return asker.pick(question, current, options, default=default, names=names, help=help)
    return choose


def _key_file(args, backups: Path) -> Path | None:
    """The Show's encryption key: from the switches, or from the record the installer left for this name."""
    if args.key_file:
        return args.key_file
    if args.serial:
        return backups / args.serial / "home-assistant.key"
    show = show_named(backups, args.name)
    if show is None:
        _no_such_show(args.name, backups)
        ui.out.note("or say which with --serial or --key-file")
        return None
    ui.out.title(f"{show.name}", f"{show.board} · serial ending {show.serial_tail}")
    return show.key_file


def _no_such_show(name: str, backups: Path) -> None:
    ui.out.fail(f"no Show named {name!r} was installed from here")
    for known in known_shows(backups):
        ui.out.note(f"installed here: {known.name!r} ({known.board}, serial ending {known.serial_tail})")


def _keeper(backups: Path):
    """Where voice_kit saves an automation or script before it replaces it."""
    def keep(what: str, config) -> str:
        folder = backups / "home-assistant"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9_.-]', '_', what)}.json"
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)
    return keep


def deploy_to_home_assistant(settings: Settings, args, *, name: str, key_file: Path, host: str | None,
                             wait_seconds: float, backups: Path) -> int:
    """Adds the Show to Home Assistant and hands it its settings through its own actions."""
    if not settings.ha_admin:
        ui.out.heading("Home Assistant")
        ui.out.same("no admin signed in, so the Show is added by hand:")
        ui.out.note(f"Settings → Devices & services → ESPHome, with the key in {key_file}")
        ui.out.note("or later, signing in as an admin:")
        ui.out.command(f"python3 tools/jarvis-show.py home-assistant --name {shlex.quote(name)}")
        return 0
    try:
        psk = key_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        ui.out.fail(f"cannot read the Show's encryption key: {exc.strerror}")
        return 1
    if not host:
        saved = key_file.parent / "address"
        if saved.is_file():
            host = saved.read_text(encoding="utf-8").strip() or None
    try:
        caldav = _caldav_account(args)
    except (SettingsError, ValueError) as exc:
        ui.out.fail(f"{exc}")
        return 1
    now_called = [name]

    def renamed(new: str) -> None:
        now_called[0] = new
        if rename_show(key_file.parent, new):
            ui.out.progress(f"the installer knows it as {new!r} from now on")

    opts = ha_api.DeployOptions(
        name=name, psk=psk, host=host, wake_word=args.wake_word or None, assistant=args.assistant or None,
        room=args.room or None, room_dashboard=args.room_dashboard, voice_extras=args.voice_extras,
        keep_replaced=_keeper(backups), calendars=args.calendars, caldav=caldav,
        ask_caldav=_caldav_asker(args), rename_entities=getattr(args, "rename_entities", None), renamed=renamed,
        settings=ha_api.DeviceSettings(dashcast=settings.dashcast, dashcast_key=settings.dashcast_key,
                                       ha_url=settings.ha_url, ha_token=settings.ha_token,
                                       music_assistant=settings.music_assistant, own_user=settings.own_ha_user,
                                       music_assistant_login=_music_assistant_login(settings)),
        wait_seconds=wait_seconds)
    ui.out.heading(f"Adding it to Home Assistant at {settings.ha_url}")
    hc = settings.ha_sign_in or ha_api.HomeAssistant(settings.ha_url, settings.ha_admin_token)
    choose = _chooser(args)
    try:
        entry_id = ha_api.deploy(hc, opts, progress=ui.out.progress, find_host=_find_host(name), choose=choose)
    except ha_api.HomeAssistantError as exc:
        ui.out.fail(f"Home Assistant: {exc}")
        return 5
    ui.out.heading("Updates")
    try:
        genbu.set_up(hc, entry_id, ha_api.node_name(hc, entry_id) or ha_api.node_slug(now_called[0]),
                     role=getattr(args, "update_role", None), choose=choose, progress=ui.out.progress)
    except ha_api.HomeAssistantError as exc:
        ui.out.progress(f"WARN: the update setup stopped part way: {exc}")
    ui.out.finish(f"{now_called[0]} is in Home Assistant and set up")
    return 0


def _caldav_account(args) -> ha_api.CalDAVAccount | None:
    """The calendar account the switches give; the password is read from its file and handed on only."""
    if not args.caldav_url:
        if args.caldav_user or args.caldav_password_file:
            raise SettingsError("--caldav-user and --caldav-password-file need --caldav-url")
        return None
    if not args.caldav_user:
        raise SettingsError("--caldav-url needs --caldav-user")
    password = read_secret_file(args.caldav_password_file, "CalDAV password") if args.caldav_password_file else ""
    return ha_api.CalDAVAccount(url=ha_api.check_caldav_url(args.caldav_url), username=args.caldav_user.strip(),
                                password=password)


def _caldav_asker(args):
    """Asks for a calendar account at the terminal; None when nobody is at it."""
    asker = _asker(args)
    if not asker.interactive:
        return None

    def filled(value: str) -> str:
        if not value.strip():
            raise ValueError("this one can't be empty")
        return value.strip()

    def ask() -> ha_api.CalDAVAccount | None:
        url = asker.text("The calendar server's address", None, ha_api.check_caldav_url, help=(
            "Nextcloud: https://cloud.example.org/remote.php/dav, iCloud: https://caldav.icloud.com. "
            "Enter to skip."))
        if not url:
            return None
        user = asker.text("User name", None, filled, help="Enter to skip.")
        if not user:
            return None
        password = asker.secret("Password", filled, help=(
            "An app password where the server has them: Nextcloud under Settings, Security; iCloud at "
            "account.apple.com. It goes to Home Assistant only."))
        if not password:
            return None
        return ha_api.CalDAVAccount(url=url, username=user, password=password)
    return ask


def _music_assistant_login(settings: Settings):
    """Makes the Show's own Music Assistant user with the installer's token, which is not passed on.
    A failure only costs the lyrics."""
    if not (settings.music_assistant and settings.music_assistant_token):
        return None

    def login(name: str) -> str | None:
        try:
            token, made = ma_api.show_token(settings.music_assistant, settings.music_assistant_token, name)
        except ma_api.MusicAssistantError as exc:
            ui.out.warn(f"Music Assistant: {exc}; lyrics won't work on the Show until this runs again")
            return None
        ui.out.done(f"Music Assistant user {ma_api.show_username(name)!r} "
                    + ("made, for the lyrics" if made else "was there; its token renewed"))
        return token
    return login


def music_assistant_metadata(settings: Settings) -> None:
    """--music-assistant-local-metadata: Music Assistant stops looking things up online. A failure
    leaves it as it was and does not stop anything else."""
    if not (settings.music_assistant and settings.music_assistant_token
            and settings.music_assistant_local_metadata):
        return
    try:
        done = ma_api.local_metadata_only(settings.music_assistant, settings.music_assistant_token)
    except ma_api.MusicAssistantError as exc:
        ui.out.warn(f"Music Assistant: {exc}; its online lookups are still on")
        ui.out.note("later:")
        ui.out.command("python3 tools/jarvis-show.py home-assistant --name NAME --music-assistant-local-metadata")
        return
    ui.out.done(f"Music Assistant: {', '.join(done)}")


def home_assistant_command(args, backups: Path) -> int:
    if not args.name:
        ui.out.fail("home-assistant requires --name, the Show's name as installed")
        return 1
    key_file = _key_file(args, backups)
    if key_file is None:
        return 1
    try:
        settings = gather(args, _asker(args), want_wifi=False)
    except SettingsError as exc:
        ui.out.fail(f"{exc}")
        return 1
    save_defaults(settings)
    music_assistant_metadata(settings)
    if not settings.ha_admin:
        ui.out.fail("adding the Show needs a Home Assistant admin: sign in as one when asked, "
                    "or give a token (--ha-admin-token-file or the keyring)")
        return 1
    return deploy_to_home_assistant(settings, args, name=args.name, key_file=key_file, host=args.host,
                                    wait_seconds=120, backups=backups)


def remove_show_command(args, backups: Path) -> int:
    """Everything of one Show out of Home Assistant and Music Assistant; see jarvis_crown/remove.py."""
    if not args.name:
        ui.out.fail("remove-show requires --name, the Show's name")
        return 1
    show = show_named(backups, args.name)
    try:
        settings = gather(args, _asker(args), want_wifi=False, want_show=False)
    except SettingsError as exc:
        ui.out.fail(f"{exc}")
        return 1
    if not settings.ha_admin:
        ui.out.fail("taking a Show out needs a Home Assistant admin: sign in as one when asked, "
                    "or give a token (--ha-admin-token-file or the keyring)")
        return 1
    hc = settings.ha_sign_in or ha_api.HomeAssistant(settings.ha_url, settings.ha_admin_token)
    music = drop = None
    if settings.music_assistant and settings.music_assistant_token:
        host, token = settings.music_assistant, settings.music_assistant_token

        def music(names, mac):
            try:
                return ma_api.show_leftovers(host, token, names, mac)
            except ma_api.MusicAssistantError as exc:
                ui.out.warn(f"Music Assistant: {exc}")
                return None

        def drop(users, players):
            try:
                return ma_api.remove_leftovers(host, token, users, players)
            except ma_api.MusicAssistantError as exc:
                return [f"{exc}"]

    ui.out.heading(f"What {args.name} has in the house")
    try:
        found = gone.find(hc, args.name, also=[show.name] if show else [], mac=show.mac if show else "",
                          music=music)
    except ha_api.HomeAssistantError as exc:
        ui.out.fail(f"Home Assistant: {exc}")
        return 5
    if show is not None and found.mac and found.mac != show.mac:
        update_show(show.folder, mac=found.mac)  # for the run after its ESPHome entry is gone
    goes, later, stays = gone.plan(found)
    for label, items, say in (("Goes now", goes, ui.out.step), ("Goes on a later run", later, ui.out.warn),
                              ("Stays", stays, ui.out.note)):
        if items:
            ui.out.heading(label)
            for item in items:
                say(item)
    if not goes:
        ui.out.done(f"nothing of {args.name} is left to remove now")
        return 0
    if not args.yes_remove:
        if args.no_questions:
            ui.out.heading("Nothing removed")
            ui.out.note("--no-questions only lists; --yes-remove removes")
            return 0
        ui.out.caution("Home Assistant cannot undo this; what goes is saved in backups/home-assistant first.")
        try:
            typed = ui.out.typed(found.name)
        except EOFError:  # no terminal (piped or closed stdin) is a no, not a crash
            print()
            typed = ""
        if typed != found.name:
            ui.out.fail("not typed; nothing was removed")
            return 1
    ui.out.heading(f"Taking {found.name} out")
    failed = gone.remove(hc, found, keep=_keeper(backups), drop=drop, progress=ui.out.progress)
    if failed:
        ui.out.fail(f"{failed} step(s) failed; the rest went. Run remove-show again to retry them.")
        return 5
    if later:
        ui.out.done(f"{found.name} is out, but for its Music Assistant player: switch the Show off, then run "
                    "remove-show again")
    else:
        ui.out.done(f"{found.name} is out of the house; it can be added again as a new device")
    return 0


def amonet_upgrade(args, backups: Path) -> int:
    """Amonet 1.x -> 2.x for a unit already in TWRP; see jarvis_crown/amonet_upgrade.py."""
    if not args.board:
        ui.out.fail("amonet-upgrade requires --board")
        return 1
    try:
        serials = adb_recovery_serials()
    except RecoveryError as exc:
        ui.out.fail(f"{exc}")
        return 2
    if len(serials) != 1:
        ui.out.fail(f"expected exactly one Show in TWRP, found {len(serials)} (adb reboot recovery first)")
        return 2

    def confirm(phrase):
        ui.out.caution("this rewrites the bootloader chain: preloader, lk, tee1, tee2, expdb, recovery and swdl.")
        ui.out.caution("keep the Show on mains power and the USB cable in until it says it is done.")
        try:
            return ui.out.typed(phrase)
        except EOFError:  # no terminal (piped or closed stdin) is a no, not a crash
            print()
            return ""

    try:
        result = upgrade_amonet(
            AdbUpgradeClient(serials[0]),
            args.board,
            (args.amonet_zip or _cached(args.board, "amonet")).resolve(),
            (args.lineage_zip or _cached(args.board, "lineage")).resolve(),
            backups / serials[0],
            confirm=confirm,
            progress=ui.out.progress,
        )
    except (UpgradeError, RecoveryError, ValueError) as exc:
        ui.out.fail(f"amonet-upgrade stopped: {exc}")
        return 4
    if result.upgraded:
        ui.out.done(f"Amonet 2.x installed and boot is plain; backup of the old state in {result.backup}")
    else:
        ui.out.done("no Amonet 1.x microloader on boot; nothing to do")
    return 0


def boot_logo_command(args, backups: Path) -> int:
    """OpenJade's logo in kaeru's wordmark slot, over the Show's SSH; see jarvis_crown/boot_logo.py."""
    if not args.name:
        ui.out.fail("boot-logo requires --name, the Show's name as installed")
        return 1
    show = show_named(backups, args.name)
    if show is None:
        _no_such_show(args.name, backups)
        return 1
    host = args.host
    if not host:
        try:
            host = (show.folder / "address").read_text(encoding="utf-8").strip()
        except OSError:
            host = ""
    if not host:
        ui.out.fail("the Show's address is not known; give it with --host")
        return 1
    saved = show.folder / "partitions" / "p7-expdb.img"
    ui.out.done(f"{show.name} is the {show.board} Show with serial ending {show.serial_tail}, at {host}")

    def confirm(phrase):
        ui.out.caution("this rewrites the logo inside the bootloader (kaeru, in expdb); nothing else is written.")
        ui.out.caution("keep the Show on power until it says it is done. A broken kaeru does not start at all.")
        try:
            return ui.out.typed(phrase)
        except EOFError:  # no terminal (piped or closed stdin) is a no, not a crash
            print()
            return ""

    try:
        done = put_logo(SshClient(host, identity_for(show.name, args.ssh_identity), show.name), show.board, saved_expdb=saved, amazon=args.amazon_logo,
                        confirm=confirm, progress=ui.out.progress)
    except BootLogoError as exc:
        ui.out.fail(f"boot-logo stopped: {exc}")
        return 4
    ui.out.done(f"{done}; it shows on the next cold start (unplug, plug back in)")
    return 0


def _stop(signum, _frame) -> None:
    # A closed terminal or a kill ends the run like Ctrl-C does, through main's finally, so the
    # installer still signs out of what it signed in to.
    raise SystemExit(128 + signum)


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _stop)
    try:
        return _main()
    finally:
        if RUN.active:
            ui.out.heading("Signing out")
            RUN.end_all(ui.out.progress)


def _main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    project = root.parent
    work = (args.work_dir or (project / "work")).resolve()
    backups = (args.backup_dir or (project / "backups")).resolve()

    # Wi-Fi recovery runs against a booted Jarvis/TECHO5 USB serial console, not fastboot. Keep it
    # inside this one front-end so an offline device never depends on HA, mDNS, or a second workflow.
    if args.command == "wifi":
        if not args.wifi:
            ui.out.fail("wifi recovery requires --wifi NETWORK")
            return 1
        cmd = [sys.executable, str(root / "tools" / "show-wifi.py"), args.wifi]
        if args.serial:
            cmd += ["--serial", args.serial]
        if args.wifi_passphrase_file:
            cmd += ["--passphrase-file", str(args.wifi_passphrase_file.resolve())]
        return subprocess.call(cmd)

    if args.command == "amonet-upgrade":
        return amonet_upgrade(args, backups)

    if args.command == "boot-logo":
        return boot_logo_command(args, backups)
    if args.command == "home-assistant":
        return home_assistant_command(args, backups)
    if args.command == "remove-show":
        return remove_show_command(args, backups)

    # identify/unlock require fastboot. install can resume from a verified TWRP
    # session only when an explicit board cross-check is supplied.
    if args.command == "identify":
        try:
            _detect_profile(args.board)
        except DeviceGateError as exc:
            ui.out.fail(f"device identity gate: {exc}")
            return 2
        return 0

    # install and unlock start from fastboot; a Show that is on Android, TWRP or Jarvis is rebooted there
    # first (only rebooted, nothing written), so nobody has to hold buttons.
    if args.command in ("install", "unlock"):
        if os.geteuid() != 0 and not _group_names() & SERIAL_GROUPS:
            ui.out.fail("this login isn't in dialout (or uucp), which the install needs for the Show's rescue "
                        "console; nothing was touched. Join it, then run this under it: "
                        "sudo usermod -aG dialout \"$USER\", then sg dialout -c '<this command>'")
            return 1
        try:
            bring_to_fastboot(name=args.name, backups=backups, host=args.host, find_host=_find_host,
                              keep_recovery=args.command == "install" and args.board is not None,
                              say=ui.out.step, identity=identity_for(args.name, args.ssh_identity))
        except ToFastbootError as exc:
            ui.out.fail(f"{exc}")
            return 2

    # Offline preflight may still be requested for a known board. Without --board it auto-detects the
    # attached first-generation Show, preserving the one-command installer experience.
    identity = None
    if args.board is not None and args.command == "preflight":
        profile = profile_for_board(args.board)
    else:
        try:
            identity, profile, identity_source = _detect_profile(
                args.board, allow_recovery=args.command == "install"
            )
        except DeviceGateError as exc:
            ui.out.fail(f"device identity gate: {exc}")
            return 2

    # A bundle checked out next to the repo wins; otherwise the one fetch-show-assets unpacked from the pinned zip.
    bundled = project / "third_party" / profile.amonet_dir_name
    amonet = (args.amonet_dir or (bundled if bundled.is_dir()
                                  else amonet_bundle_dir(default_cache_dir(), profile.board))).resolve()

    try:
        amonet_hashes = load_hash_manifest(args.amonet_hashes)
        twrp_sha256 = _validate_optional_sha("TWRP SHA-256", args.twrp_sha256)
        boot_sha256 = _validate_optional_sha("boot SHA-256", args.boot_sha256)
    except ValueError as exc:
        ui.out.fail(f"{exc}")
        return 1

    if args.command == "install":
        if not args.name:
            ui.out.fail("install requires --name")
            return 1
        if bool(args.rootfs) != bool(args.rootfs_sha256):
            ui.out.fail("--rootfs and --rootfs-sha256 go together")
            return 1
        try:
            rootfs_sha256 = _validate_optional_sha("rootfs SHA-256", args.rootfs_sha256)
        except ValueError as exc:
            ui.out.fail(f"{exc}")
            return 1

        # Whatever was not passed in comes from the pinned asset cache and the signed release, for the
        # board that was detected, before a single question is asked: a missing file stops the run here.
        ui.out.heading(f"Install files for the {profile.model}")
        try:
            files = _install_files(args, profile.board, amonet, work)
        except (InstallFilesError, ReleaseError) as exc:
            ui.out.fail(f"{exc}")
            return 1
        amonet = files["amonet_dir"]
        lineage_zip, boot_image = files["lineage"], files["boot"]
        rootfs, rootfs_sha256 = files.get("rootfs", args.rootfs), files.get("rootfs_sha256", rootfs_sha256)

        # Everything the Show starts with, asked before anything on it is touched.
        try:
            settings = gather(args, _asker(args), want_root_password=True)
        except SettingsError as exc:
            ui.out.fail(f"{exc}")
            return 1
        save_defaults(settings)
        ui.out.heading("What the Show is set up with")
        for line in settings.summary():
            label, _, value = line.partition(": ")
            if value:
                ui.out.field(label, value, pad=16)
            else:
                ui.out.note(line, indent=2)

        try:
            ssh_key = _show_ssh_key(args)
        except (ShowKeyError, OSError) as exc:
            ui.out.fail(f"{exc}")
            return 1

        def confirm_unlock(p):
            build = identity.lk_build_desc if identity is not None else None
            ui.out.caution(f"unlocking {p.model} runs the board-specific Amonet exploit.")
            ui.out.caution(f"detected LK build: {build or 'UNREADABLE'}")
            ui.out.caution("bootloader exploits inherently carry brick risk; keep mains power connected and do not interrupt writes.")
            return ui.out.typed(p.unlock_confirmation)

        def confirm_install(p):
            ui.out.caution("the next stage formats userdata for Lineage vendor staging, then converts system into the A/B slot store.")
            ui.out.caution("the verified recovery backup already exists; do not disconnect power/USB during these writes.")
            return ui.out.typed(p.install_confirmation)

        with secret_files(settings) as files:
            inputs = InstallInputs(
                board=profile.board,
                repo_root=root,
                amonet_dir=amonet,
                lineage_zip=lineage_zip.resolve(),
                work_dir=work,
                backups_dir=backups,
                name=args.name,
                boot_image=boot_image.resolve(),
                boot_sha256=boot_sha256,
                rootfs=rootfs.resolve(),
                rootfs_sha256=rootfs_sha256,
                update_channel=args.channel,
                twrp_sha256=twrp_sha256,
                amonet_hashes=amonet_hashes,
                wifi=settings.wifi,
                wifi_passphrase_file=files.get("wifi_passphrase") or (
                    args.wifi_passphrase_file.resolve() if args.wifi_passphrase_file and settings.wifi else None),
                ha_url=settings.ha_url if settings.ha_token else None,
                ha_token_file=files.get("ha_token") if settings.ha_url else None,
                dashcast=settings.dashcast,
                dashcast_key_file=files.get("dashcast_key") if settings.dashcast else None,
                music_assistant=settings.music_assistant,
                root_password_file=files.get("root_password"),
                ssh_key=ssh_key,
                expected_fastboot_serial=identity.serial if identity is not None else None,
            )
            try:
                result = run_install_flow(
                    inputs,
                    confirm_unlock=confirm_unlock,
                    confirm_install=confirm_install,
                    progress=lambda stage: ui.out.step(STAGES.get(stage, stage)),
                    initial_identity=identity if identity_source == "recovery" else None,
                )
            except (FlowError, DeviceGateError, UnlockError, RuntimeError) as exc:
                ui.out.fail(f"install stopped: {exc}")
                return 4
        ui.out.done(f"{result.profile.product_id} is installed")
        music_assistant_metadata(settings)
        if not settings.wifi:
            ui.out.note("Join Wi-Fi on the Show's screen, then add it to Home Assistant with:", indent=2)
            ui.out.command(f"python3 tools/jarvis-show.py home-assistant --name {shlex.quote(args.name)}")
            return 0
        key_file = backups / result.recovery.adb_serial / "home-assistant.key"
        return deploy_to_home_assistant(settings, args, name=args.name, key_file=key_file, host=None,
                                        wait_seconds=300, backups=backups)

    checks = run_preflight(
        repo_root=root,
        amonet_dir=amonet,
        lineage_zip=args.lineage_zip.resolve() if args.lineage_zip else None,
        work_dir=work,
        backup_dir=backups,
        board=profile.board,
    )
    print_checks(checks)
    if not preflight_ok(checks):
        ui.out.fail("host/input preflight failed; no write was attempted")
        return 1
    if args.command == "preflight":
        if identity is None:
            ui.out.done(f"offline {profile.model} host/input preflight complete; no device was queried or modified")
        else:
            ui.out.done(f"auto-detected {profile.model} host/input preflight complete; no device was modified")
        return 0

    # unlock reaches here only after the live target was auto-detected above.
    assert identity is not None
    if identity.unlocked:
        ui.out.done(f"{profile.model} already unlocked; Amonet will not run")
        return 0

    ui.out.caution(f"the next stage intentionally runs Amonet for {profile.board}.")
    ui.out.caution(f"detected LK build: {identity.lk_build_desc or 'UNREADABLE'}")
    ui.out.caution("bootloader exploits inherently carry brick risk; keep mains power connected and do not interrupt writes.")
    confirmation = ui.out.typed(profile.unlock_confirmation)
    try:
        result = unlock_show(
            identity,
            amonet,
            confirmation=confirmation,
            expected_hashes=amonet_hashes,
        )
    except UnlockError as exc:
        ui.out.fail(f"{profile.board} unlock: {exc}")
        return 3
    if not result.identity.unlocked:
        ui.out.fail("unlock result was not proven")
        return 3
    ui.out.done("unlock_status=true re-confirmed read-only after Amonet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
