from nautobot.apps.api import (
    BaseModelSerializer,
    NautobotModelSerializer,
    TaggedModelSerializerMixin,
)

from ..models import Backend, Flow, Run


class BackendSerializer(TaggedModelSerializerMixin, NautobotModelSerializer):
    class Meta:
        model = Backend
        fields = "__all__"

    def validate(self, data):
        data = super().validate(data)
        instance = Backend(**{k: v for k, v in data.items() if k != "tags"})
        instance.clean()
        return data


class FlowSerializer(TaggedModelSerializerMixin, NautobotModelSerializer):
    class Meta:
        model = Flow
        fields = "__all__"


class RunSerializer(BaseModelSerializer):
    class Meta:
        model = Run
        exclude = ("input", "plan", "output")
        read_only_fields = [f.name for f in Run._meta.fields]
