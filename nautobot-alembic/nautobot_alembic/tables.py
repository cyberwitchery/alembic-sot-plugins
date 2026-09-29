import django_tables2 as tables
from nautobot.apps.tables import BaseTable, ButtonsColumn, ChoiceFieldColumn, ToggleColumn

from .models import Backend, Flow, Run


class BackendTable(BaseTable):
    pk = ToggleColumn()
    name = tables.Column(linkify=True)
    secrets_group = tables.Column(linkify=True)
    actions = ButtonsColumn(Backend)

    class Meta(BaseTable.Meta):
        model = Backend
        fields = (
            "pk",
            "name",
            "kind",
            "secrets_group",
            "external_adapter",
            "description",
            "actions",
        )
        default_columns = ("pk", "name", "kind", "secrets_group", "description", "actions")


class FlowTable(BaseTable):
    pk = ToggleColumn()
    name = tables.Column(linkify=True)
    git_repository = tables.Column(linkify=True)
    source = tables.Column(linkify=True)
    target = tables.Column(linkify=True)
    actions = ButtonsColumn(Flow)

    class Meta(BaseTable.Meta):
        model = Flow
        fields = (
            "pk",
            "name",
            "git_repository",
            "root",
            "inventory",
            "source",
            "map_spec",
            "target",
            "allow_delete",
            "description",
            "actions",
        )
        default_columns = (
            "pk",
            "name",
            "git_repository",
            "inventory",
            "source",
            "target",
            "actions",
        )


class RunTable(BaseTable):
    pk = ToggleColumn()
    run = tables.Column(accessor="pk", linkify=True, verbose_name="Run", orderable=False)
    flow = tables.Column(linkify=True)
    status = ChoiceFieldColumn()
    requested_by = tables.Column()
    decided_by = tables.Column()

    class Meta(BaseTable.Meta):
        model = Run
        fields = (
            "pk",
            "run",
            "flow",
            "kind",
            "status",
            "requested_by",
            "decided_by",
            "created",
            "finished_at",
        )
        default_columns = ("pk", "run", "flow", "kind", "status", "requested_by", "created")

    def render_run(self, record):
        return str(record)
