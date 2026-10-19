# Updates come from Home Assistant, not GitHub (GENBU-225)

Status: design, 2026-10-19. Nothing built yet. Builds on [update-leader.md](update-leader.md) (one house
version) and is the update part of GENBU-226 (Shows reach only Home Assistant).

Jade's ask: copy how [Spegel](https://spegel.dev/docs/overview/) works for Kubernetes. Genbu Assistant reads
each model's release from GitHub once and hands it to the Shows of that model, so the Shows stop going to
GitHub one by one.

## What happens today

- Home Assistant reads the channel's `manifest.json` every hour and offers the update on its card
  (GENBU-258).
- Every following Show then reads `/<version>/manifest-<board>.json` and its `.sig` from GitHub itself
  (`update.FetchTag`), checks the signature, and downloads its root filesystem from GitHub: 35.8 MB for
  dev.172 on cronos. Four Shows means four downloads of the same file.
- So every Show needs a router rule that lets it out to GitHub on 443. That rule is the only reason the
  IoT Shows can reach the internet at all, apart from NTP.
- Rate limits: our own code already avoids api.github.com (`channel.go`). GitHub tightened its limits
  for anonymous traffic in May 2025 ([changelog](https://github.blog/changelog/2025-05-08-updated-rate-limits-for-unauthenticated-requests/)).
  It names cloning, the REST API and raw downloads, not release downloads. Whether release downloads have
  a limit, and what it is, is not documented. We don't know it, so we shouldn't depend on it.

## What we take from Spegel, and what we don't

Spegel runs a small read-only registry on every node. It serves the images that node already has, finds
which node has what through a shared table, and falls back to the real registry when nobody has it.

| Spegel | Genbu |
|---|---|
| Every node serves what it already pulled, nothing stored twice | Genbu Assistant keeps one copy per release and board for the house. The Shows don't serve each other (see "Show to Show" below) |
| Content is looked up by digest, and the puller checks the digest, so a peer can't swap content | Files are looked up by the sha256 the **signed** manifest names. The Show checks signature, sha256 and size exactly as it does today |
| `latest` is never taken from a peer, because it moves; it always comes from upstream ([k3s docs](https://documentation.suse.com/cloudnative/k3s/latest/en/installation/registry-mirror.html)) | The channel manifest (the moving one) is read only by Genbu Assistant, from GitHub. Shows only ask for one exact version, and `FetchTag` already refuses a manifest that names another version |
| A shared table (DHT) says which node has which image, entries expire after 10 minutes | No table. Genbu Assistant already knows every Show's version and is the one place that decides (update-leader.md) |
| Not found anywhere → pull from upstream | Not in the cache → Genbu Assistant pulls it from GitHub while the Show waits. A Show with no Home Assistant at all still goes to GitHub, as today |
| Stateless: lose it and nothing breaks | The cache can be deleted at any time; it fills again from GitHub |
| All nodes trust each other equally; a node can poison images for the others | Not copied. Shows trust only the release key compiled into echod. Home Assistant can hold an update back but can't hand a Show anything the key didn't sign |

## How it works

**Genbu Assistant (the mirror)**

1. It reads the channel manifest every hour, as now. It also reads the manifest of every board in the
   house (`manifest.json` for crown and checkers, `manifest-cronos.json` for cronos), with `.sig`. To know
   the boards, it needs each Show's board: from the device's model in HA, or a new board sensor
   (to check which).
2. It checks each manifest's signature against the release key, the same one echod has. A bad
   signature is an error on the card and a repair, never "up to date".
3. When a release is offered on the card, it downloads what the house's boards need, ahead of the Install
   press. For Linux Shows that's `rootfs[arch]`; for Fire OS Shows it's `binaries[arch]`. It only follows
   URLs that a signed manifest names, and only on the GitHub release hosts. It writes to a temp file,
   checks sha256 and size, then renames the file to `<sha256>`.
4. It keeps the house version, the one before it (for going back) and the one on offer. Everything else
   is deleted. Before downloading it checks free space; if space is short, that's a repair.
5. The cache lives in a folder that HA's backups leave out, so a backup doesn't grow by 70 MB per
   release. Still to check on the live box: which folder that is.
6. It serves two read-only paths over HA's own HTTPS, behind the normal HA login:
   - `GET /api/genbu/release/<version>/<manifest name>` and the same path with `.sig`
   - `GET /api/genbu/blob/<sha256>`, where the name must be exactly 64 hex characters. It sends the
     file with Range support, so a Show on weak Wi-Fi can resume. At most 2 downloads run at once, so
     HA stays responsive.

**The Show**

1. A Show that has HA access (`hass.json`: URL and token, already there for weather and pictures) asks
   Home Assistant first. It uses the URL from `hass.json`, not an address HA sends it. That keeps the
   rule in `channel.go`: Home Assistant can't aim a root updater at somebody else's server.
2. It reads the manifest and `.sig` for the house version from HA. Everything after that is today's code:
   the release key, the version match, the product and board, the version floor.
3. It downloads the file by sha256 from HA and checks sha256 and size before writing a slot.
4. If HA answers 404 or 503, the Show waits and tries again. If HA can't be reached at all, the Show
   falls back to GitHub, as it does today. In Jade's house that fallback fails harmlessly once the router
   rule is gone, and the Show reports that on the card. The router rule is the real switch, not the code.

## Show to Show (the part of Spegel we leave out)

Spegel's peers serving each other would mean every Show opens a port that runs as root. Each Show would
also need to keep its last image around to serve it. With Home Assistant already on the LAN, it saves
almost nothing: one 36 MB file from HA to 4 Shows on the local network. Spegel's own trust model assumes
every node is equally trusted, and IoT speakers are not. **My recommendation: don't build it.** Revisit
it only if HA turns out to be too slow to serve a house.

## Security pass

- Sources: Spegel's docs and FAQ, the k3s embedded-mirror docs (equal trust, image poisoning, why
  `latest` is excluded), and Spegel's GitHub advisories page, which lists none. I found no CVE that names
  Spegel.
- **A compromised HA** can hold updates back or move the house to an old release. It can already do both
  today, and the `min_align` floor still applies. It can't change a file: the hash comes from the signed
  manifest.
- **Something else on the LAN** can't write the cache. Genbu Assistant checks every file before keeping
  it, and the Show checks again.
- **The endpoints** only read files named by a hash and need HA's login. They can't be made to fetch
  arbitrary URLs: HA only fetches URLs from signed manifests, and only from GitHub's release hosts.
  Strict names rule out path tricks.
- **Disk:** the number of versions kept is capped, and free space is checked first.
- **Not new, but worth knowing:** a Show's HA token is a full long-lived token, because HA has no scoped
  tokens. That's true today too.
- **Gain:** once the GitHub rule is removed, a Show taken over by someone can no longer reach the
  internet. That's the main security win.

## Gains, losses, what breaks

- Gains: one download per house instead of one per Show. Updates at LAN speed, resumable. They still work
  while GitHub is down, if the release was already fetched. No more rate-limit question. The IoT Shows
  close to the internet.
- Losses: 100 to 200 MB on the HA box. If HA is down, there are no updates (with the GitHub rule gone).
  More code in the integration.
- Breaks unless handled:
  - Shows on older firmware still use GitHub, so keep the router rule until every Show has updated once
    to a build that asks HA.
  - The cache would bloat HA backups if it's put in the wrong folder.
  - Install guide 732 and firewall pages 180/378 need updating, but only after the change is done.

## Steps

1. Genbu Assistant: per-board manifests, signature check in HA, cache with pruning, the two paths,
   errors as repairs. This would be genbu 1.5.0.
2. echod: HA first for `FetchTag` and the download, GitHub as fallback.
3. Card and GENBU-215: show "downloaded for the house" and per-Show progress.
4. Tests:
   - A cache serving a changed file is refused at HA and at the Show.
   - The wrong version in a manifest is refused.
   - With HA down, the Show falls back to GitHub.
   - A 64-hex-only name check, and a cut-off download that resumes.
5. Jade: remove the GitHub router rules per Show, then update wiki 732, 180 and 378.
