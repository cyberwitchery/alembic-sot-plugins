from core.api.serializers import DataSourceSerializer
from netbox.api.fields import ChoiceField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from users.api.serializers import UserSerializer

from ..models import Backend, BackendKindChoices, Flow, Run, RunKindChoices, RunStatusChoices

API = "plugins-api:netbox_alembic-api"


class BackendSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name=f"{API}:backend-detail")
    kind = ChoiceField(choices=BackendKindChoices)

    class Meta:
        model = Backend
        fields = (
            "id",
            "url",
            "display",
            "name",
            "kind",
            "config",
            "credential",
            "external_adapter",
            "description",
            "comments",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "kind")

    def validate(self, data):
        data = super().validate(data)
        if self.nested:
            return data
        instance = self.instance or Backend()
        for name, value in data.items():
            if name not in ("tags", "custom_fields"):
                setattr(instance, name, value)
        instance.clean()
        return data


class FlowSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name=f"{API}:flow-detail")
    data_source = DataSourceSerializer(nested=True)
    target = BackendSerializer(nested=True)

    class Meta:
        model = Flow
        fields = (
            "id",
            "url",
            "display",
            "name",
            "data_source",
            "root",
            "inventory",
            "target",
            "allow_delete",
            "no_adopt",
            "state_key",
            "description",
            "comments",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")


class RunSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name=f"{API}:run-detail")
    flow = FlowSerializer(nested=True, read_only=True)
    kind = ChoiceField(choices=RunKindChoices, read_only=True)
    status = ChoiceField(choices=RunStatusChoices, read_only=True)
    requested_by = UserSerializer(nested=True, read_only=True)
    decided_by = UserSerializer(nested=True, read_only=True)

    class Meta:
        model = Run
        fields = (
            "id",
            "url",
            "display",
            "flow",
            "kind",
            "status",
            "requested_by",
            "decided_by",
            "decided_at",
            "planned_at",
            "apply_started_at",
            "finished_at",
            "input_sha256",
            "plan_sha256",
            "approved_sha256",
            "summary",
            "drift_report",
            "apply_report",
            "alembic_version",
            "error",
            "created",
            "last_updated",
        )
        read_only_fields = fields
        brief_fields = ("id", "url", "display", "status")
