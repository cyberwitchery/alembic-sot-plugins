"""seeds the demo netbox. runs inside the container via `manage.py shell`."""

from core.models import DataSource
from netbox_alembic.models import Backend, Flow
from users.models import Token, User

# demo-only credentials for a local netbox; they are in demo/README.md too.
for name in ("engineer", "reviewer"):
    user = User.objects.create_user(username=name, password="demo", is_superuser=True)

service = User.objects.create_user(username="alembic", is_superuser=True)
token = Token(user=service, write_enabled=True, description="alembic writes to netbox with this")
token.save()

source = DataSource.objects.create(
    name="fabric",
    type="git",
    source_url="file:///opt/demo-repo",
    # the netbox form always stores the backend's parameters; its detail page
    # reads them and fails on null.
    parameters={},
    description="the fabric inventory, in git",
)
source.sync()

target = Backend.objects.create(
    name="netbox",
    kind="netbox",
    config={"url": "http://netbox:8080"},
    credential="netbox",
    description="this netbox, as the worker reaches it",
)
Flow.objects.create(
    name="fabric",
    data_source=source,
    inventory="fabric.yaml",
    target=target,
    description="the fabric inventory, converged into netbox",
)

print(f"NETBOX_TOKEN nbt_{token.key}.{token.token}")
