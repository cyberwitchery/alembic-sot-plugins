import django_filters
from django.db.models import Q
from netbox.filtersets import ChangeLoggedModelFilterSet, NetBoxModelFilterSet

from .models import Backend, Flow, Run, RunKindChoices, RunStatusChoices


class BackendFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = Backend
        fields = ("id", "name", "kind", "credential", "external_adapter")

    def search(self, queryset, name, value):
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class FlowFilterSet(NetBoxModelFilterSet):
    target_id = django_filters.ModelMultipleChoiceFilter(queryset=Backend.objects.all())

    class Meta:
        model = Flow
        fields = ("id", "name", "data_source", "inventory", "allow_delete")

    def search(self, queryset, name, value):
        match = Q(name__icontains=value) | Q(description__icontains=value)
        return queryset.filter(match | Q(inventory__icontains=value))


class RunFilterSet(ChangeLoggedModelFilterSet):
    flow_id = django_filters.ModelMultipleChoiceFilter(queryset=Flow.objects.all())
    kind = django_filters.MultipleChoiceFilter(choices=RunKindChoices)
    status = django_filters.MultipleChoiceFilter(choices=RunStatusChoices)

    class Meta:
        model = Run
        fields = ("id", "plan_sha256", "input_sha256")

    def search(self, queryset, name, value):
        return queryset.filter(Q(flow__name__icontains=value) | Q(error__icontains=value))
