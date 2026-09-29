#!/bin/sh
# end-to-end check against the dev nautobot: the superuser's token goes to the
# worker, then nautobot-e2e.py drives plan, approval, apply, stale, drift, an
# approval workflow and nautobot as the source.
set -eu
here="$(dirname "$0")"

token="0123456789abcdef0123456789abcdef01234567"
ALEMBIC_NAUTOBOT_TOKEN="$token" "$here/nautobot-compose" up -d --force-recreate worker >/dev/null 2>&1
"$here/nautobot-compose" exec -T nautobot sh -c 'cat > /tmp/nautobot-e2e.py' < "$here/nautobot-e2e.py"
"$here/nautobot-compose" exec -T nautobot nautobot-server nbshell --quiet-load \
  --command "exec(open('/tmp/nautobot-e2e.py').read())"
