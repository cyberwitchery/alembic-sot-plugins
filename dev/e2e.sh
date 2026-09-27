#!/bin/sh
# end-to-end check against the dev netbox: a service token for netbox itself goes
# to the worker, then e2e.py drives plan, approval, apply, stale and drift.
set -eu
here="$(dirname "$0")"

token=$("$here/manage" shell -c "
from users.models import Token, User
user, _ = User.objects.get_or_create(username='alembic-service', defaults={'is_superuser': True})
token = Token(user=user, write_enabled=True, description='alembic e2e service'); token.save()
print(f'TOKEN nbt_{token.key}.{token.token}')
" 2>/dev/null | sed -n 's/^TOKEN //p')

ALEMBIC_NETBOX_TOKEN="$token" "$here/compose" up -d --force-recreate netbox-worker >/dev/null 2>&1
"$here/compose" exec -T -w /opt/plugins netbox \
  /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < "$here/e2e.py" 2>&1 \
  | grep -v -E 'loaded config|objects imported'
