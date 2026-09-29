from alembic_runner import RunStatus
from alembic_runner.review import BUSY, PLAN_AGAIN, drift_rows, group_ops, input_names
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from netbox.object_actions import BulkExport
from netbox.ui import attrs, layout, panels
from netbox.views import generic
from netbox.views.generic.base import BaseObjectView
from utilities.views import GetReturnURLMixin, register_model_view

from . import actions, filtersets, forms, tables
from .models import Backend, Flow, Run, RunKindChoices

#
# panels
#


class BackendPanel(panels.ObjectAttributesPanel):
    title = _("Backend")
    name = attrs.TextAttr("name")
    kind = attrs.ChoiceAttr("kind")
    credential = attrs.TextAttr("credential", style="font-monospace")
    external_adapter = attrs.TextAttr("external_adapter", style="font-monospace")
    description = attrs.TextAttr("description")


class FlowPanel(panels.ObjectAttributesPanel):
    title = _("Flow")
    name = attrs.TextAttr("name")
    data_source = attrs.RelatedObjectAttr("data_source", linkify=True)
    root = attrs.TextAttr("root", style="font-monospace")
    inventory = attrs.TextAttr("inventory", style="font-monospace")
    source = attrs.RelatedObjectAttr("source", linkify=True)
    map_spec = attrs.TextAttr("map_spec", style="font-monospace")
    target = attrs.RelatedObjectAttr("target", linkify=True)
    allow_delete = attrs.BooleanAttr("allow_delete")
    no_adopt = attrs.BooleanAttr("no_adopt")
    state_key = attrs.TextAttr("state_key", style="font-monospace")
    description = attrs.TextAttr("description")


class RunPanel(panels.ObjectAttributesPanel):
    title = _("Run")
    flow = attrs.RelatedObjectAttr("flow", linkify=True)
    kind = attrs.ChoiceAttr("kind")
    status = attrs.ChoiceAttr("status")
    requested_by = attrs.TextAttr("requested_by", label=_("Requested by"))
    decided_by = attrs.TextAttr("decided_by", label=_("Decided by"))
    planned_at = attrs.DateTimeAttr("planned_at", label=_("Planned at"))
    decided_at = attrs.DateTimeAttr("decided_at", label=_("Decided at"))
    finished_at = attrs.DateTimeAttr("finished_at", label=_("Finished at"))
    alembic_version = attrs.TextAttr("alembic_version", label=_("Alembic version"))
    input_sha256 = attrs.TextAttr("input_sha256", label=_("Input SHA-256"), style="font-monospace")
    plan_sha256 = attrs.TextAttr("plan_sha256", label=_("Plan SHA-256"), style="font-monospace")


class TemplatedPanel(panels.ObjectPanel):
    """an object panel whose template gets the view's extra context too."""

    def __init__(self, template_name, keys=(), **kwargs):
        super().__init__(**kwargs)
        self.template_name = template_name
        self.keys = keys

    def get_context(self, context):
        return {**super().get_context(context), **{k: context.get(k) for k in self.keys}}


#
# backends
#


@register_model_view(Backend, "list", path="", detail=False)
class BackendListView(generic.ObjectListView):
    queryset = Backend.objects.all()
    table = tables.BackendTable
    filterset = filtersets.BackendFilterSet
    filterset_form = forms.BackendFilterForm


@register_model_view(Backend)
class BackendView(generic.ObjectView):
    queryset = Backend.objects.all()
    layout = layout.SimpleLayout(
        left_panels=[BackendPanel(), panels.CommentsPanel()],
        right_panels=[panels.JSONPanel("config", title=_("Config"))],
        bottom_panels=[
            panels.ObjectsTablePanel(
                model="netbox_alembic.Flow",
                filters={"target_id": lambda ctx: ctx["object"].pk},
                exclude_columns=["target"],
            ),
        ],
    )


@register_model_view(Backend, "add", detail=False)
@register_model_view(Backend, "edit")
class BackendEditView(generic.ObjectEditView):
    queryset = Backend.objects.all()
    form = forms.BackendForm


@register_model_view(Backend, "delete")
class BackendDeleteView(generic.ObjectDeleteView):
    queryset = Backend.objects.all()


@register_model_view(Backend, "bulk_delete", path="delete", detail=False)
class BackendBulkDeleteView(generic.BulkDeleteView):
    queryset = Backend.objects.all()
    filterset = filtersets.BackendFilterSet
    table = tables.BackendTable


#
# flows
#


@register_model_view(Flow, "list", path="", detail=False)
class FlowListView(generic.ObjectListView):
    queryset = Flow.objects.select_related("data_source", "source", "target")
    table = tables.FlowTable
    filterset = filtersets.FlowFilterSet
    filterset_form = forms.FlowFilterForm


@register_model_view(Flow)
class FlowView(generic.ObjectView):
    queryset = Flow.objects.select_related("data_source", "source", "target")
    layout = layout.SimpleLayout(
        left_panels=[FlowPanel(), panels.CommentsPanel()],
        right_panels=[TemplatedPanel("netbox_alembic/panels/flow_actions.html", title=_("Runs"))],
        bottom_panels=[
            panels.ObjectsTablePanel(
                model="netbox_alembic.Run",
                filters={"flow_id": lambda ctx: ctx["object"].pk},
                exclude_columns=["flow"],
            ),
        ],
    )


@register_model_view(Flow, "add", detail=False)
@register_model_view(Flow, "edit")
class FlowEditView(generic.ObjectEditView):
    queryset = Flow.objects.all()
    form = forms.FlowForm


@register_model_view(Flow, "delete")
class FlowDeleteView(generic.ObjectDeleteView):
    queryset = Flow.objects.all()


@register_model_view(Flow, "bulk_delete", path="delete", detail=False)
class FlowBulkDeleteView(generic.BulkDeleteView):
    queryset = Flow.objects.all()
    filterset = filtersets.FlowFilterSet
    table = tables.FlowTable


class ActionView(GetReturnURLMixin, BaseObjectView):
    """a POST-only view that performs one action and returns to the object."""

    def get(self, request, pk):
        return redirect(get_object_or_404(self.queryset, pk=pk).get_absolute_url())

    def post(self, request, pk):
        obj = get_object_or_404(self.queryset, pk=pk)
        try:
            result = self.perform(obj, request.user)
        except PermissionDenied as e:
            messages.error(request, str(e))
            return redirect(obj.get_absolute_url())
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(obj.get_absolute_url())
        messages.success(request, self.message)
        return redirect(result.get_absolute_url())


@register_model_view(Flow, "plan")
class FlowPlanView(ActionView):
    queryset = Flow.objects.all()
    message = _("Plan queued")

    def get_required_permission(self):
        # the flow only has to be visible; starting a run is checked below, since
        # requiring add_run here would restrict the flows by "add" as well.
        return "netbox_alembic.view_flow"

    def perform(self, flow, user):
        if not user.has_perm("netbox_alembic.add_run"):
            raise PermissionDenied(_("You may not start runs."))
        kind = self.request.POST.get("kind", RunKindChoices.PLAN)
        if kind not in (RunKindChoices.PLAN, RunKindChoices.DRIFT):
            raise ValidationError(_("Unknown run kind."))
        return actions.request_run(flow, user, kind)


#
# runs
#


@register_model_view(Run, "list", path="", detail=False)
class RunListView(generic.ObjectListView):
    queryset = Run.objects.select_related("flow", "requested_by", "decided_by")
    table = tables.RunTable
    filterset = filtersets.RunFilterSet
    filterset_form = forms.RunFilterForm
    actions = (BulkExport,)


@register_model_view(Run)
class RunView(generic.ObjectView):
    queryset = Run.objects.select_related("flow", "flow__target", "requested_by", "decided_by")
    actions = ()
    layout = layout.SimpleLayout(
        left_panels=[RunPanel()],
        right_panels=[
            TemplatedPanel(
                "netbox_alembic/panels/run_decision.html",
                keys=("can_decide", "decide_reason", "can_resume", "can_plan_again", "busy"),
                title=_("Decision"),
            ),
            TemplatedPanel("netbox_alembic/panels/run_summary.html", title=_("Summary")),
        ],
        bottom_panels=[
            TemplatedPanel(
                "netbox_alembic/panels/run_ops.html",
                keys=("op_groups", "drift_rows"),
                title=_("Changes"),
            ),
            TemplatedPanel(
                "netbox_alembic/panels/run_output.html", keys=("show_output",), title=_("Output")
            ),
        ],
    )

    def get_extra_context(self, request, instance):
        user = request.user
        reason = actions.refusal(instance, user)
        names = input_names(
            (instance.input or {}).get(instance.input_entry or instance.flow.inventory)
        )
        waiting = instance.status == RunStatus.AWAITING_APPROVAL.value
        busy = instance.status in BUSY
        return {
            "busy": busy,
            "show_output": instance.status in (RunStatus.FAILED.value, RunStatus.STALE.value)
            or instance.status == RunStatus.APPLY_FAILED.value,
            "op_groups": group_ops(instance.plan, names),
            "drift_rows": drift_rows(instance.drift_report, names),
            "can_decide": waiting and reason is None,
            "decide_reason": reason if waiting else None,
            "can_plan_again": instance.status in PLAN_AGAIN
            and user.has_perm("netbox_alembic.add_run"),
            "can_resume": instance.status == RunStatus.APPLY_FAILED.value
            and user.has_perm("netbox_alembic.approve_run", instance),
        }


class RunActionView(ActionView):
    queryset = Run.objects.all()

    def get_required_permission(self):
        return "netbox_alembic.view_run"


@register_model_view(Run, "approve")
class RunApproveView(RunActionView):
    message = _("Approved; apply queued")

    def perform(self, run, user):
        return actions.approve(run, user)


@register_model_view(Run, "reject")
class RunRejectView(RunActionView):
    message = _("Rejected")

    def perform(self, run, user):
        return actions.reject(run, user)


@register_model_view(Run, "resume")
class RunResumeView(RunActionView):
    message = _("Resume queued")

    def perform(self, run, user):
        return actions.resume(run, user)


@register_model_view(Run, "plan_json", path="plan.json")
class RunPlanDownloadView(BaseObjectView):
    queryset = Run.objects.all()

    def get_required_permission(self):
        return "netbox_alembic.view_run"

    def get(self, request, pk):
        run = get_object_or_404(self.queryset, pk=pk)
        response = HttpResponse(run.plan, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="run-{run.pk}-plan.json"'
        return response
