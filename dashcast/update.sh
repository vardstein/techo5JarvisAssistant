#!/bin/sh
# Builds dashcast from a commit of this repository and swaps the running container for it, with the
# running one's settings: its address and port, shared memory, memory limit, restart policy and
# environment.
#
#   sh update.sh <commit>          on the machine dashcast runs on, as a user who may use docker
#
# The container running now is stopped and kept as <name>-previous, so going back is the two
# commands this prints at the end.
# DASHCAST_CONTAINER (default dashcast) and DASHCAST_REPO (default this repository) change what it
# looks at. DASHCAST_MEMORY (like 2560m or 3g) sets the memory limit; without it the running
# container's limit is kept, and one that has none gets 2560m (README, "When it gets stuck").
set -eu

commit=${1:?usage: update.sh <commit>}
name=${DASHCAST_CONTAINER:-dashcast}
repo=${DASHCAST_REPO:-https://github.com/vardstein/techo5JarvisAssistant.git}
image=openjade/techo5-dashcast:$commit

old=$(docker inspect "$name" --format '{{.Config.Image}}')
ports=$(docker inspect "$name" --format \
	'{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}-p {{if .HostIp}}{{.HostIp}}:{{end}}{{.HostPort}}:{{$p}} {{end}}{{end}}')
shm=$(docker inspect "$name" --format '{{.HostConfig.ShmSize}}')
restart=$(docker inspect "$name" --format '{{.HostConfig.RestartPolicy.Name}}')
[ -n "$restart" ] && [ "$restart" != no ] || restart=unless-stopped

# The memory limit, with swap the same, so a tab that grows without end is killed inside the
# container rather than the machine swapping for hours. Only a plain size is passed on to docker.
memory=${DASHCAST_MEMORY:-$(docker inspect "$name" --format '{{.HostConfig.Memory}}')}
[ "$memory" != 0 ] || memory=2560m
case $memory in
'' | *[!0-9bkmgBKMG]* | [!0-9]* | *[bkmgBKMG]?*)
	echo "update.sh: DASHCAST_MEMORY must be a size like 2560m or 3g, not '$memory'" >&2
	exit 2
	;;
esac

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
git clone -q --filter=blob:none "$repo" "$dir/src"
git -C "$dir/src" checkout -q "$commit"
docker build -q -t "$image" "$dir/src/dashcast"

# The settings in the README and docs/jarvis-show-platform.md, as the running container has them.
# The file holds the token and the key, so it is readable by nobody else and goes with the temporary
# folder.
umask 077
docker inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' |
	grep -E '^(HA_URL|HA_TOKEN|DASHCAST_KEY|LISTEN|CHROME|DASHCAST_WARM|DASHCAST_TAB_MEMORY|LANG|JARVIS_SHOW_BOARD|JARVIS_SHOW_UI_GENERATION|JARVIS_CROWN_UI_GENERATION)=' >"$dir/env"

# The one before the last update goes; the one running now stays, stopped, for going back.
docker rm -f "$name-previous" >/dev/null 2>&1 || true
docker stop "$name" >/dev/null
docker rename "$name" "$name-previous"
# $ports is several words on purpose.
# shellcheck disable=SC2086
docker run -d --name "$name" --restart "$restart" $ports --shm-size "$shm" \
	--memory "$memory" --memory-swap "$memory" \
	--env-file "$dir/env" "$image" >/dev/null
sleep 5
docker ps --filter "name=^$name\$" --format '{{.Names}} {{.Image}} {{.Status}}'
docker inspect "$name" --format 'memory limit {{.HostConfig.Memory}} bytes, swap included {{.HostConfig.MemorySwap}}'
docker logs --tail 5 "$name"
echo
echo "The one before ($old) is stopped and kept as $name-previous. To go back:"
echo "  docker rm -f $name && docker rename $name-previous $name && docker start $name"
