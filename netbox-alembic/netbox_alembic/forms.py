from core.models import DataSource
from django import forms
from django.utils.translation import gettext_lazy as _
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms.fields import CommentField, DynamicModelChoiceField, JSONField
from utilities.forms.rendering import FieldSet

from .models import Backend, BackendKindChoices, Flow, Run, RunKindChoices, RunStatusChoices


class BackendForm(NetBoxModelForm):
    config = JSONField(label=_("config"), required=False)
    comments = CommentField()

    fieldsets = (
        FieldSet("name", "kind", "is_self", "description", "tags", name=_("backend")),
        FieldSet("config", "credential", "external_adapter", name=_("connection")),
    )

    class Meta:
        model = Backend
        fields = (
            "name",
            "kind",
            "is_self",
            "config",
            "credential",
            "external_adapter",
            "description",
            "comments",
            "tags",
        )


class BackendFilterForm(NetBoxModelFilterSetForm):
    model = Backend
    fieldsets = (FieldSet("q", "filter_id", "tag"), FieldSet("kind", "is_self"))
    kind = forms.MultipleChoiceField(choices=BackendKindChoices, required=False)
    is_self = forms.NullBooleanField(required=False, label=_("this netbox"))


class FlowForm(NetBoxModelForm):
    data_source = DynamicModelChoiceField(queryset=DataSource.objects.all())
    target = DynamicModelChoiceField(queryset=Backend.objects.all())
    comments = CommentField()

    fieldsets = (
        FieldSet("name", "description", "tags", name=_("flow")),
        FieldSet("data_source", "root", "inventory", name=_("source")),
        FieldSet("target", "allow_delete", "no_adopt", "state_key", name=_("target")),
    )

    class Meta:
        model = Flow
        fields = (
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
