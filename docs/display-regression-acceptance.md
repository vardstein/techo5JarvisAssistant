# Jarvis Show display regression acceptance

The old `livingRoomEcho8` deployment accumulated several competing ways to remove Home Assistant's
header: dashboard `kiosk_mode`, a `jarvis-edge-to-edge.js` frontend module, TECHO5's header switch and
Dashcast's own browser injection. Warm browser tabs could then resurrect frontend state from before a
change. The visible symptom was a blank/black strip across the top even when the header itself was no
longer visible.

Jarvis Show has one owner for this behavior: **Dashcast appliance mode**.

## Code-level acceptance

For a first-generation Show, Dashcast ignores the device-supplied browser geometry and enforces:

| Board | Viewport | Path | Kiosk |
|---|---:|---|---|
| Crown / Show 8 1st gen | 1280x800 | `/jarvis-display` | on |
| Checkers / Show 5 1st gen | 960x480 | `/jarvis-display` | on |

The pre-page browser script owns all browser chrome removal. It hides Home Assistant's top bars and
sidebar, zeros header/safe-area/top/left offsets, and forces the relevant HA hosts/views to a full
`100vh`. No HA-side `kiosk_mode`, card-mod rule or `jarvis-edge-to-edge.js` module is required.

Warm pages are keyed by board geometry/path/kiosk state **and** `JARVIS_SHOW_UI_GENERATION`. The
`dashboard_reload` action sends a one-shot cold hello so Dashcast discards a matching parked tab.

CI exercises the exact kiosk script inside headless Chromium against a mock HA shadow-DOM tree at
both first-generation Show resolutions. This catches the class of bug where the header is hidden but
its height/padding remains reserved.

## Physical-screen acceptance — still required

Source-level/browser tests are necessary but not sufficient. Before v1 release acceptance, each real
board must open a **fresh cold session** and produce a screenshot/photo showing the complete panel.

Required physical observations:

- no black/blank strip at the top;
- no Home Assistant header or sidebar;
- dashboard touches still map correctly at the panel edges;
- Crown renders at 1280x800 and Checkers at 960x480 without cropping/scaling artifacts;
- the existing Jarvis dashboard's weather/WIND/room/clock card geometry is unchanged by browser
  chrome removal;
- after bumping UI generation or invoking `dashboard_reload`, the newly opened page has the same
  geometry and no stale header state.

The HA custom-card geometry itself lives on the HA server, not in this firmware repository. Therefore
it cannot truthfully be marked physically accepted from repository tests alone.

## Recording live evidence

After each board has been checked physically, record the screenshot/photo and explicit observations with
`tools/live-acceptance.py j38`. The helper stores only the evidence path, size and SHA-256 plus the
operator confirmations; it does not inspect an image and cannot invent a visual PASS. The state file
defaults to `~/.local/state/jarvis-show/live-acceptance.json` with mode `0600`.

Example flags mirror every physical observation above; every board the product lists in the board
catalogue (`catalogue/products/`), later ones like `cronos` too, must be recorded before
`tools/live-acceptance.py verify` can pass.
