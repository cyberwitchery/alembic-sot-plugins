from core.models import DataSource
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms.fields import CommentField, DynamicModelChoiceField, JSONField
from utilities.forms.rendering import FieldSet

from .models import Backend, Flow, Run, RunKindChoices, RunStatusChoices
from .settings import kind_choices


class BackendForm(NetBoxModelForm):
    kind = forms.ChoiceField(label=_("Kind"), choices=kind_choices)
    config = JSONField(label=_("Config"), required=False)
    comments = CommentField()

    fieldsets = (
        FieldSet("name", "kind", "description", "tags", name=_("Backend")),
        FieldSet("config", "credential", name=_("Connection")),
    )

    class Meta:
        model = Backend
        fields = (
            "name",
            "kind",
            "config",
            "credential",
            "description",
            "comments",
            "tags",
        )


class BackendFilterForm(NetBoxModelFilterSetForm):
    model = Backend
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet(
            "kind",
        ),
    )
    kind = forms.MultipleChoiceField(choices=kind_choices, required=False)


class FlowForm(NetBoxModelForm):
    data_source = DynamicModelChoiceField(queryset=DataSource.objects.all())
    source = DynamicModelChoiceField(queryset=Backend.objects.all(), required=False)
    target = DynamicModelChoiceField(queryset=Backend.objects.all())
    comments = CommentField()

    fieldsets = (
        FieldSet("name", "description", "tags", name=_("Flow")),
        FieldSet("data_source", "root", "inventory", "source", "map_spec", name=_("Source")),
        FieldSet("target", "allow_delete", "no_adopt", "state_key", name=_("Target")),
    )

    class Meta:
        model = Flow
        fields = (
            "name",
            "data_source",
            "root",
            "inventory",
            "source",
            "map_spec",
            "target",
            "allow_delete",
            "no_adopt",
            "state_key",
            "description",
            "comments",
            "tags",
        )


class FlowFilterForm(NetBoxModelFilterSetForm):
    model = Flow
    fieldsets = (FieldSet("q", "filter_id", "tag"), FieldSet("target_id", "data_source_id"))
    target_id = DynamicModelChoiceField(queryset=Backend.objects.all(), required=False)
    data_source_id = DynamicModelChoiceField(queryset=DataSource.objects.all(), required=False)


class RunFilterForm(NetBoxModelFilterSetForm):
    model = Run
    fieldsets = (FieldSet("q", "filter_id"), FieldSet("flow_id", "kind", "status"))
    flow_id = DynamicModelChoiceField(queryset=Flow.objects.all(), required=False)
    kind = forms.MultipleChoiceField(choices=RunKindChoices, required=False)
    status = forms.MultipleChoiceField(choices=RunStatusChoices, required=False)
