from django import forms
from nautobot.apps.forms import DynamicModelChoiceField, NautobotFilterForm, NautobotModelForm
from nautobot.extras.models import GitRepository, SecretsGroup

from .models import Backend, BackendKindChoices, Flow, Run, RunKindChoices, RunStatusChoices


class BackendForm(NautobotModelForm):
    secrets_group = DynamicModelChoiceField(queryset=SecretsGroup.objects.all(), required=False)

    class Meta:
        model = Backend
        fields = (
            "name",
            "kind",
            "config",
            "secrets_group",
            "external_adapter",
            "description",
            "tags",
        )


class BackendFilterForm(NautobotFilterForm):
    model = Backend
    q = forms.CharField(required=False, label="Search")
    kind = forms.MultipleChoiceField(choices=BackendKindChoices, required=False)


class FlowForm(NautobotModelForm):
    git_repository = DynamicModelChoiceField(queryset=GitRepository.objects.all())
    source = DynamicModelChoiceField(queryset=Backend.objects.all(), required=False)
    target = DynamicModelChoiceField(queryset=Backend.objects.all())

    class Meta:
        model = Flow
        fields = (
            "name",
            "git_repository",
            "root",
            "inventory",
            "source",
            "map_spec",
            "target",
            "allow_delete",
            "no_adopt",
            "state_key",
            "description",
            "tags",
        )


class FlowFilterForm(NautobotFilterForm):
    model = Flow
    q = forms.CharField(required=False, label="Search")


class RunFilterForm(NautobotFilterForm):
    model = Run
    q = forms.CharField(required=False, label="Search")
    kind = forms.MultipleChoiceField(choices=RunKindChoices, required=False)
    status = forms.MultipleChoiceField(choices=RunStatusChoices, required=False)
