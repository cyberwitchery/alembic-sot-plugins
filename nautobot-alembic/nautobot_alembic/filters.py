import django_filters
from nautobot.apps.filters import (
    NaturalKeyOrPKMultipleChoiceFilter,
    NautobotFilterSet,
    SearchFilter,
)

from .models import Backend, BackendKindChoices, Flow, Run, RunKindChoices, RunStatusChoices


class BackendFilterSet(NautobotFilterSet):
    q = SearchFilter(filter_predicates={"name": "icontains", "description": "icontains"})
    kind = django_filters.MultipleChoiceFilter(choices=BackendKindChoices)

    class Meta:
        model = Backend
        fields = ("id", "name", "kind", "secrets_group", "external_adapter", "tags")


class FlowFilterSet(NautobotFilterSet):
    q = SearchFilter(filter_predicates={"name": "icontains", "inventory": "icontains"})
    target = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=Backend.objects.all(), to_field_name="name"
    )
    source = NaturalKeyOrPKMultipleChoiceFilter(
        queryset=Backend.objects.all(), to_field_name="name"
    )

    class Meta:
        model = Flow
        fields = ("id", "name", "git_repository", "inventory", "allow_delete", "tags")


class RunFilterSet(NautobotFilterSet):
    q = SearchFilter(filter_predicates={"flow__name": "icontains", "error": "icontains"})
    flow = NaturalKeyOrPKMultipleChoiceFilter(queryset=Flow.objects.all(), to_field_name="name")
    kind = django_filters.MultipleChoiceFilter(choices=RunKindChoices)
    status = django_filters.MultipleChoiceFilter(choices=RunStatusChoices)

    class Meta:
        model = Run
        fields = ("id", "plan_sha256", "input_sha256")
