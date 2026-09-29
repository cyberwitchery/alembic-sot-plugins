from alembic_runner import RunStatus
from alembic_runner.review import BUSY, PLAN_AGAIN, drift_rows, group_ops, input_names
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.html import format_html
from nautobot.apps import ui, views
from rest_framework.decorators import action

from . import actions, filters, forms, tables
from .api import serializers
from .models import Backend, Flow, Run, RunKindChoices, RunStatusChoices
from .settings import kind_choices


def kind_label(value):
    """a backend kind as the form names it."""
    return dict(kind_choices()).get(value, value)


def status_badge(value):
    """a run's status as the coloured badge nautobot uses for choices."""
    label = RunStatusChoices.as_dict().get(value, value)
    css = RunStatusChoices.CSS_CLASSES.get(value, "secondary")
    return format_html('<span class="badge bg-{}">{}</span>', css, label)


def _perform(request, obj, fn, *args, success):
    """run an action for the ui: a message either way, then back to the object."""
    try:
        result = fn(*args)
    except PermissionDenied as e:
        messages.error(request, str(e))
        return redirect(obj.get_absolute_url())
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect(obj.get_absolute_url())
    messages.success(request, success)
    return redirect(result.get_absolute_url())


class BackendUIViewSet(
    views.ObjectListViewMixin,
    views.ObjectDetailViewMixin,
    views.ObjectEditViewMixin,
    views.ObjectDestroyViewMixin,
    views.ObjectBulkDestroyViewMixin,
    views.ObjectChangeLogViewMixin,
    views.ObjectNotesViewMixin,
):
    queryset = Backend.objects.select_related("secrets_group")
    filterset_class = filters.BackendFilterSet
    filterset_form_class = forms.BackendFilterForm
    form_class = forms.BackendForm
    serializer_class = serializers.BackendSerializer
    table_class = tables.BackendTable
    # no bulk import: a backend is set up by hand, one at a time.
    action_buttons = ("add", "export")

    object_detail_content = ui.ObjectDetailContent(
        panels=(
            ui.ObjectFieldsPanel(
                section=ui.SectionChoices.LEFT_HALF,
                weight=100,
                fields=["name", "kind", "secrets_group", "description"],
                value_transforms={"kind": [kind_label]},
            ),
            ui.ObjectFieldsPanel(
                section=ui.SectionChoices.RIGHT_HALF,
                weight=100,
                label="Connection",
                fields=["config"],
            ),
        )
    )


class FlowUIViewSet(
    views.ObjectListViewMixin,
    views.ObjectDetailViewMixin,
    views.ObjectEditViewMixin,
    views.ObjectDestroyViewMixin,
    views.ObjectBulkDestroyViewMixin,
    views.ObjectChangeLogViewMixin,
    views.ObjectNotesViewMixin,
):
    queryset = Flow.objects.select_related("git_repository", "source", "target")
    filterset_class = filters.FlowFilterSet
    filterset_form_class = forms.FlowFilterForm
    form_class = forms.FlowForm
    serializer_class = serializers.FlowSerializer
    table_class = tables.FlowTable
    action_buttons = ("add", "export")

    object_detail_content = ui.ObjectDetailContent(
        panels=(
            ui.ObjectFieldsPanel(
                section=ui.SectionChoices.LEFT_HALF,
                weight=100,
                fields=[
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
                ],
            ),
            ui.Panel(
                section=ui.SectionChoices.RIGHT_HALF,
                weight=100,
                label="Runs",
                body_content_template_path="nautobot_alembic/panels/flow_actions.html",
            ),
            ui.ObjectsTablePanel(
                section=ui.SectionChoices.FULL_WIDTH,
                weight=200,
                table_class=tables.RunTable,
                table_filter="flow",
                exclude_columns=["flow"],
            ),
        )
    )

    @action(detail=True, methods=["post"], url_path="plan", custom_view_base_action="view")
    def plan(self, request, pk):
        flow = get_object_or_404(self.queryset.restrict(request.user, "view"), pk=pk)
        if not request.user.has_perm("nautobot_alembic.add_run"):
            messages.error(request, "You may not start runs.")
            return redirect(flow.get_absolute_url())
        kind = request.POST.get("kind", RunKindChoices.PLAN)
        if kind not in (RunKindChoices.PLAN, RunKindChoices.DRIFT):
            messages.error(request, "Unknown run kind.")
            return redirect(flow.get_absolute_url())
        return _perform(
            request, flow, actions.request_run, flow, request.user, kind, success="Plan queued."
        )


class RunUIViewSet(
    views.ObjectListViewMixin,
    views.ObjectDetailViewMixin,
    views.ObjectChangeLogViewMixin,
):
    queryset = Run.objects.select_related("flow", "flow__target", "requested_by", "decided_by")
    filterset_class = filters.RunFilterSet
    filterset_form_class = forms.RunFilterForm
    serializer_class = serializers.RunSerializer
    table_class = tables.RunTable
    action_buttons = ("export",)

    object_detail_content = ui.ObjectDetailContent(
        panels=(
            ui.ObjectFieldsPanel(
                section=ui.SectionChoices.LEFT_HALF,
                weight=100,
                fields=[
                    "flow",
                    "kind",
                    "status",
                    "requested_by",
                    "decided_by",
                    "planned_at",
                    "decided_at",
                    "finished_at",
                    "alembic_version",
                    "input_sha256",
                    "plan_sha256",
                ],
                value_transforms={"status": [status_badge]},
            ),
            ui.Panel(
                section=ui.SectionChoices.RIGHT_HALF,
                weight=100,
                label="Decision",
                body_content_template_path="nautobot_alembic/panels/run_decision.html",
            ),
            ui.Panel(
                section=ui.SectionChoices.RIGHT_HALF,
                weight=200,
                label="Summary",
                body_content_template_path="nautobot_alembic/panels/run_summary.html",
            ),
            ui.Panel(
                section=ui.SectionChoices.FULL_WIDTH,
                weight=100,
                label="Changes",
                body_content_template_path="nautobot_alembic/panels/run_changes.html",
            ),
            ui.Panel(
                section=ui.SectionChoices.FULL_WIDTH,
                weight=200,
                label="Output",
                body_content_template_path="nautobot_alembic/panels/run_output.html",
            ),
        )
    )

    def get_extra_context(self, request, instance=None):
        context = super().get_extra_context(request, instance)
        if self.action != "retrieve" or instance is None:
            return context
        user = request.user
        waiting = instance.status == RunStatus.AWAITING_APPROVAL.value
        workflow = actions.workflow_for(instance)
        reason = actions.refusal(instance, user)
        text = (instance.input or {}).get(instance.input_entry or instance.flow.inventory)
        names = input_names(text)
        context.update(
            {
                "workflow": workflow,
                "can_decide": waiting and workflow is None and reason is None,
                "decide_reason": reason if waiting and workflow is None else None,
                "can_resume": instance.status == RunStatus.APPLY_FAILED.value
                and user.has_perm("nautobot_alembic.approve_run", instance),
                "can_plan_again": instance.status in PLAN_AGAIN
                and user.has_perm("nautobot_alembic.add_run"),
                "busy": instance.status in BUSY,
                "show_output": instance.status
                in (RunStatus.FAILED.value, RunStatus.STALE.value, RunStatus.APPLY_FAILED.value),
                "op_groups": group_ops(instance.plan, names),
                "drift_rows": drift_rows(instance.drift_report, names),
            }
        )
        return context

    def _run(self, request, pk):
        return get_object_or_404(self.queryset.restrict(request.user, "view"), pk=pk)

    @action(detail=True, methods=["post"], url_path="approve", custom_view_base_action="view")
    def approve(self, request, pk):
        run = self._run(request, pk)
        return _perform(
            request, run, actions.approve, run, request.user, success="Approved; apply queued."
        )

    @action(detail=True, methods=["post"], url_path="reject", custom_view_base_action="view")
    def reject(self, request, pk):
        run = self._run(request, pk)
        return _perform(request, run, actions.reject, run, request.user, success="Rejected.")

    @action(detail=True, methods=["post"], url_path="resume", custom_view_base_action="view")
    def resume(self, request, pk):
        run = self._run(request, pk)
        return _perform(request, run, actions.resume, run, request.user, success="Resume queued.")

    @action(
        detail=True,
        methods=["get"],
        url_path="plan.json",
        url_name="plan_json",
        custom_view_base_action="view",
    )
    def plan_json(self, request, pk):
        run = self._run(request, pk)
        response = HttpResponse(run.plan, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="run-{run.pk}-plan.json"'
        return response
