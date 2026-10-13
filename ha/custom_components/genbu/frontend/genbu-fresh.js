// Reloads a page whose Jarvis cards are older than the ones Genbu serves (GENBU-147).
//
// Home Assistant writes the cards' addresses into the page, and its service worker hands out the page
// it kept while it fetches the new one for next time. So after a Genbu update a Show's dashboard, and
// any tab left open through the restart, goes on running the old cards. This module's address carries
// a hash of all the cards; it asks Home Assistant over the websocket, which the service worker leaves
// alone, for the current one, again whenever the connection comes back, and reloads when they differ.
//
// Only once nobody has touched the page for two minutes, so nothing being typed is lost, and at most
// three times per version: the first reload can still get the kept page.
{
  const mine = new URL(import.meta.url).searchParams.get("v");
  const QUIET = 120000;
  const TRIES = 3;
  let touched = 0;
  let waiting = null;
  for (const type of ["pointerdown", "keydown", "wheel"]) {
    addEventListener(type, () => { touched = Date.now(); }, { capture: true, passive: true });
  }

  const tries = (version) => {
    try {
      return Number(sessionStorage.getItem(`genbu-fresh-${version}`) || 0);
    } catch (e) {
      return TRIES; // no session storage: no way to count, so no reloading
    }
  };

  const reloadWhenQuiet = (version) => {
    if (waiting || tries(version) >= TRIES) return;
    const go = () => {
      const left = touched + QUIET - Date.now();
      if (left > 0) {
        waiting = setTimeout(go, left);
        return;
      }
      try {
        sessionStorage.setItem(`genbu-fresh-${version}`, String(tries(version) + 1));
      } catch (e) {
        return;
      }
      location.reload();
    };
    waiting = setTimeout(go, 5000); // time for the service worker to keep the new page
  };

  // Right after a restart the websocket can be up before Genbu is, so an unknown command is asked again.
  const check = async (conn, left = 10) => {
    try {
      const { version } = await conn.sendMessagePromise({ type: "genbu/frontend" });
      if (version && version !== mine) reloadWhenQuiet(version);
    } catch (e) {
      if (left > 0) setTimeout(() => check(conn, left - 1), 30000);
    }
  };

  customElements.whenDefined("home-assistant").then(() => {
    const start = () => {
      const app = document.querySelector("home-assistant");
      const conn = app && app.hass && app.hass.connection;
      if (!conn) {
        setTimeout(start, 1000);
        return;
      }
      check(conn);
      conn.addEventListener("ready", () => check(conn));
    };
    start();
  });
}
