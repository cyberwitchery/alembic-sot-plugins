"""seeds the demo netbox. runs inside the container via `manage.py shell`."""

from core.models import DataSource
from netbox_alembic.models import Backend, Flow
from users.models import Token, User

# demo-only credentials for a local netbox; they are in demo/README.md too.
for name in ("engineer", "reviewer"):
    user = User.objects.create_user(username=name, password="demo", is_superuser=True)

service = User.objects.create_user(username="alembic", is_superuser=True)
token = Token(user=service, write_enabled=True, description="alembic runs against this netbox")
token.save()

source = DataSource.objects.create(
    name="fabric",
    type="git",
    source_url="file:///opt/demo-repo",
    description="the fabric inventory, in git",
)
source.sync()

target = Backend.objects.create(
    name="this netbox", kind="netbox", is_self=True, description="alembic writes here"
)
Flow.objects.create(
    name="fabric",
    data_source=source,
    inventory="fabric.yaml",
    target=target,
    description="the fabric inventory, converged into this netbox",
)

print(f"SELF_TOKEN nbt_{token.key}.{token.token}")
