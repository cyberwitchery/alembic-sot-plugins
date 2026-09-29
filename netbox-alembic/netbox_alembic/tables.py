import django_tables2 as tables
from django.utils.translation import gettext_lazy as _
from netbox.tables import NetBoxTable, columns

from .models import Backend, Flow, Run


class BackendTable(NetBoxTable):
    name = tables.Column(linkify=True)
    kind = tables.Column(verbose_name=_("Kind"), accessor="kind_label", order_by=("kind",))
    tags = columns.TagColumn(url_name="plugins:netbox_alembic:backend_list")

    class Meta(NetBoxTable.Meta):
        model = Backend
        fields = ("pk", "id", "name", "kind", "credential", "description", "tags")
        default_columns = ("name", "kind", "credential", "description")


class FlowTable(NetBoxTable):
    name = tables.Column(linkify=True)
    data_source = tables.Column(linkify=True)
    source = tables.Column(linkify=True)
    target = tables.Column(linkify=True)
    allow_delete = columns.BooleanColumn()
    tags = columns.TagColumn(url_name="plugins:netbox_alembic:flow_list")

    class Meta(NetBoxTable.Meta):
        model = Flow
        fields = (
            "pk",
            "id",
            "name",
            "data_source",
            "root",
            "inventory",
            "source",
            "map_spec",
            "target",
            "allow_delete",
            "description",
            "tags",
        )
        default_columns = ("name", "data_source", "inventory", "source", "target", "allow_delete")


class RunTable(NetBoxTable):
    id = tables.Column(linkify=True, verbose_name=_("Run"))
    flow = tables.Column(linkify=True)
    kind = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    requested_by = tables.Column()
    decided_by = tables.Column()
    actions = columns.ActionsColumn(actions=())

    class Meta(NetBoxTable.Meta):
        model = Run
        fields = (
            "pk",
            "id",
            "flow",
            "kind",
            "status",
            "summary",
            "requested_by",
            "decided_by",
            "created",
            "finished_at",
        )
        default_columns = ("id", "flow", "kind", "status", "requested_by", "created")
