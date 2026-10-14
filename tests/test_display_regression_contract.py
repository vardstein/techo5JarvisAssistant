from pathlib import Path
import html
import json
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BROWSER_GO = ROOT / "dashcast/browser.go"
SERVE_GO = ROOT / "dashcast/serve.go"
MAIN_GO = ROOT / "dashcast/main.go"
WARM_GO = ROOT / "dashcast/warm.go"
DASHBOARD_GO = ROOT / "echod/internal/feature/dashboard/dashboard.go"
STREAM_GO = ROOT / "echod/internal/feature/dashboard/stream.go"


def kiosk_script() -> str:
    text = BROWSER_GO.read_text(encoding="utf-8")
    match = re.search(r"const kioskScript = `\n(.*?)`\n", text, re.S)
    if not match:
        raise AssertionError("cannot locate kioskScript")
    return match.group(1)


class DisplayRegressionContractTests(unittest.TestCase):
    def test_appliance_profiles_force_first_gen_geometry_and_jarvis_path(self):
        src = SERVE_GO.read_text(encoding="utf-8")
        self.assertRegex(src, r'"crown":\s*\{1280, 800\}')
        self.assertRegex(src, r'"checkers":\s*\{960, 480\}')
        self.assertRegex(src, r'"cronos":\s*\{960, 480\}')
        self.assertIn('h.W, h.H = size.w, size.h', src)
        self.assertIn('h.Path = "/jarvis-display"', src)
        self.assertIn('h.Kiosk = true', src)

    def test_kiosk_css_owns_header_sidebar_safe_area_and_full_height(self):
        script = kiosk_script()
        for want in (
            "--header-height:0px!important",
            "--safe-area-inset-top:0px!important",
            "ha-sidebar{display:none!important;width:0!important;min-width:0!important}",
            "padding-top:0!important",
            "margin-top:0!important",
            "min-height:100vh!important",
            "height:100vh!important",
        ):
            self.assertIn(want, script)

    def test_warm_tabs_are_generation_bound_and_support_one_shot_cold_reload(self):
        serve = SERVE_GO.read_text(encoding="utf-8")
        main = MAIN_GO.read_text(encoding="utf-8")
        warm = WARM_GO.read_text(encoding="utf-8")
        dashboard = DASHBOARD_GO.read_text(encoding="utf-8")
        stream = STREAM_GO.read_text(encoding="utf-8")
        self.assertIn('|g=%s", h.Name, h.W, h.H, h.Path, h.Kiosk, music, cfg.generation', serve)
        self.assertIn('JARVIS_SHOW_UI_GENERATION', main)
        self.assertIn('func (p *warmPool) discard(key string)', warm)
        self.assertIn('Name: "dashboard_reload"', dashboard)
        self.assertIn('hello["cold"] = true', stream)

    def test_cards_measure_again_once_the_header_is_gone(self):
        # wall-clock-card sizes itself to the room below its top edge when it is drawn; drawn under
        # the header, it left the header's 56px empty at the bottom until the window changed.
        script = kiosk_script()
        self.assertIn('dispatchEvent(new Event("resize"))', script)
        self.assertIn("if (moved)", script)

    def test_no_external_header_hack_is_part_of_the_product_source(self):
        # The old live deployment used HA-side kiosk_mode / jarvis-edge-to-edge workarounds. The
        # product source must not depend on those: Dashcast is the single owner of browser chrome.
        for rel in ("dashcast", "echod"):
            for path in (ROOT / rel).rglob("*"):
                if not path.is_file() or path.suffix not in {".go", ".js", ".html", ".md"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("jarvis-edge-to-edge.js", text, str(path))

    def test_exact_kiosk_script_is_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not available")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(kiosk_script())
            path = f.name
        try:
            proc = subprocess.run([node, "--check", path], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_exact_kiosk_script_fills_mock_ha_dom_in_chromium(self):
        chrome = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        if not chrome:
            self.skipTest("Chromium is not available")
        # Some stripped CI/sandbox Chromium packages never complete even a trivial --dump-dom. Do
        # not turn that host defect into a product failure; the Dashcast image CI sets its own hard
        # real-browser requirement. A timed-out Chromium leaves helpers writing into its profile after
        # the main process is killed, so the folder may not empty cleanly; that is not a failure either.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            smoke = Path(td) / "smoke.html"
            smoke.write_text(
                "<!doctype html><body>before<script>document.body.textContent='JARVIS-CHROMIUM-JS-OK';</script>",
                encoding="utf-8",
            )
            try:
                probe = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", f"--user-data-dir={td}/profile", "--dump-dom", smoke.as_uri()],
                                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            except subprocess.TimeoutExpired:
                self.skipTest("local Chromium cannot complete a headless smoke test")
            if probe.returncode != 0:
                self.skipTest("local Chromium headless smoke test failed")
            probe_dom = html.unescape(probe.stdout)
            if not re.search(r"<body[^>]*>\s*JARVIS-CHROMIUM-JS-OK\s*</body>", probe_dom):
                self.skipTest("local Chromium dump-dom does not execute JavaScript")
        script = kiosk_script()
        for width, height in ((1280, 800), (960, 480)):
            with self.subTest(width=width, height=height), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                page = Path(td) / "ha.html"
                fixture = """<!doctype html><meta charset=utf-8><style>html,body{margin:0;width:100%;height:100%}</style>
<body><home-assistant></home-assistant><script>
const reportJarvisRuntimeError=(value)=>{
 const node=document.createElement('pre');
 node.id='jarvis-runtime-error';
 node.textContent=String(value);
 parent.document.body.replaceChildren(node);
};
window.addEventListener('error',(event)=>{
 reportJarvisRuntimeError(event.error && event.error.stack ? event.error.stack : event.message);
});
window.addEventListener('unhandledrejection',(event)=>{
 const reason=event.reason;
 reportJarvisRuntimeError(reason && reason.stack ? reason.stack : reason);
});
</script><script>
const ha=document.querySelector('home-assistant'); const har=ha.attachShadow({mode:'open'});
const main=document.createElement('home-assistant-main'); har.appendChild(main); const mr=main.attachShadow({mode:'open'});
const sidebar=document.createElement('ha-sidebar'); sidebar.style.width='256px'; mr.appendChild(sidebar);
const view=document.createElement('div'); view.id='view'; view.style.paddingTop='64px'; mr.appendChild(view);
const panel=document.createElement('ha-panel-lovelace'); view.appendChild(panel); const pr=panel.attachShadow({mode:'open'});
const hui=document.createElement('hui-root'); pr.appendChild(hui); const hr=hui.attachShadow({mode:'open'});
const header=document.createElement('div'); header.className='header'; header.textContent='HEADER'; hr.appendChild(header);
const topFixed=document.createElement('ha-top-app-bar-fixed'); pr.appendChild(topFixed); const tr=topFixed.attachShadow({mode:'open'});
const topbar=document.createElement('header'); topbar.className='top-app-bar'; topbar.textContent='TOP'; tr.appendChild(topbar);
// Production refreshes every second forever. The test executes that callback once so headless
// Chromium can exit after validating the exact production script/CSS.
window.setInterval=(fn)=>{fn(); return 1;};
""" + script + """
const result={
 viewportWidth:innerWidth, viewportHeight:innerHeight,
 shell:!!mr.getElementById('jarvis-show-shell'), huiStyle:!!hr.getElementById('techo5-kiosk'), topStyle:!!tr.getElementById('techo5-kiosk'),
 sidebar:getComputedStyle(sidebar).display, sidebarWidth:getComputedStyle(sidebar).width,
 viewPad:getComputedStyle(view).paddingTop, viewHeight:getComputedStyle(view).height,
 mainHeight:getComputedStyle(main).height, huiHeight:getComputedStyle(hui).height,
 header:getComputedStyle(header).display, topbar:getComputedStyle(topbar).display
};
parent.document.body.textContent='RESULT:'+JSON.stringify(result);
</script>"""
                # Unified headless Chrome's --window-size includes browser chrome, so it does not
                # set the page viewport. A fixed-size frame gives the exact native CSS viewport
                # independently of the host window, like Dashcast's device-metrics override.
                page.write_text(
                    f'<!doctype html><body><iframe style="border:0;width:{width}px;height:{height}px" '
                    f'srcdoc="{html.escape(fixture, quote=True)}"></iframe>',
                    encoding="utf-8",
                )
                proc = subprocess.run(
                    [chrome, "--headless", "--no-sandbox", "--disable-gpu", f"--window-size={width},{height}",
                     f"--user-data-dir={td}/profile", "--virtual-time-budget=1500", "--dump-dom", page.as_uri()],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr[-1000:])
                dumped = html.unescape(proc.stdout)
                runtime_error = re.search(r'<pre id="jarvis-runtime-error">(.*?)</pre>', dumped, re.S)
                self.assertIsNone(
                    runtime_error,
                    runtime_error.group(1).strip() if runtime_error else "",
                )
                match = re.search(r"RESULT:(\{.*?\})", dumped)
                self.assertIsNotNone(match, proc.stdout[-2000:])
                result = json.loads(match.group(1))
                self.assertEqual(result["viewportWidth"], width)
                self.assertEqual(result["viewportHeight"], height)
                self.assertTrue(result["shell"])
                self.assertTrue(result["huiStyle"])
                self.assertTrue(result["topStyle"])
                self.assertEqual(result["sidebar"], "none")
                self.assertEqual(result["sidebarWidth"], "0px")
                self.assertEqual(result["viewPad"], "0px")
                self.assertEqual(result["header"], "none")
                self.assertEqual(result["topbar"], "none")
                self.assertEqual(result["viewHeight"], f"{height}px")
                self.assertEqual(result["mainHeight"], f"{height}px")
                self.assertEqual(result["huiHeight"], f"{height}px")


if __name__ == "__main__":
    unittest.main()
