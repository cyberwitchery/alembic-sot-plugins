#!/bin/sh
# builds the demo from scratch: a git repo holding the fabric inventory, a netbox
# with the plugin, two users, a git data source on that repo and one flow.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
git_demo() { git -C "$here/.repo" -c user.name=demo -c user.email=demo@example.invalid "$@"; }

echo "resetting the demo netbox and repo"
"$here/compose" down -v >/dev/null 2>&1 || true
rm -rf "$here/.repo" "$here/.state"
mkdir -p "$here/.repo" "$here/.state"
cp -R "$here/fabric/." "$here/.repo/"
git_demo init -q -b main
git_demo add -A
git_demo commit -q -m "fabric: fra1 and ber1, ber1-leaf02 planned"

echo "starting netbox (the first start runs migrations, a few minutes)"
"$here/compose" up -d --build --wait --wait-timeout 1200 >/dev/null 2>&1 \
  || { echo "netbox did not start; see demo/compose logs netbox" >&2; exit 1; }

token=$("$here/compose" exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py \
  shell < "$here/seed.py" 2>/dev/null | sed -n 's/^SELF_TOKEN //p')
[ -n "$token" ] || { echo "seeding failed; run demo/seed.py by hand to see why" >&2; exit 1; }
printf '%s' "$token" > "$here/.state/self-token"

# the worker reads the service token from its environment.
"$here/compose" up -d --force-recreate --wait netbox-worker >/dev/null 2>&1
echo "ready: http://localhost:${NETBOX_PORT:-8020}/plugins/alembic/flows/"
echo "log in as engineer / demo and reviewer / demo (use two browser profiles)"
