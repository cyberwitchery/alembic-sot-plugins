import json
from collections import defaultdict

from alembic_runner import RunStatus
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
from .settings import setting

#
# panels
#


class BackendPanel(panels.ObjectAttributesPanel):
    title = _("Backend")
    name = attrs.TextAttr("name")
    kind = attrs.ChoiceAttr("kind")
    is_self = attrs.BooleanAttr("is_self", label=_("This NetBox"))
    credential = attrs.TextAttr("credential", style="font-monospace")
    external_adapter = attrs.TextAttr("external_adapter", style="font-monospace")
    description = attrs.TextAttr("description")


class FlowPanel(panels.ObjectAttributesPanel):
    title = _("Flow")
    name = attrs.TextAttr("name")
    data_source = attrs.RelatedObjectAttr("data_source", linkify=True)
    root = attrs.TextAttr("root", style="font-monospace")
    inventory = attrs.TextAttr("inventory", style="font-monospace")
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
    queryset = Flow.objects.select_related("data_source", "target")
    table = tables.FlowTable
    filterset = filtersets.FlowFilterSet
    filterset_form = forms.FlowFilterForm


@register_model_view(Flow)
class FlowView(generic.ObjectView):
    queryset = Flow.objects.select_related("data_source", "target")
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
        return "netbox_alembic.add_run"

    def perform(self, flow, user):
        kind = self.request.POST.get("kind", RunKindChoices.PLAN)
        if kind not in (RunKindChoices.PLAN, RunKindChoices.DRIFT):
            raise ValidationError(_("unknown run kind"))
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


BUSY = frozenset(
    s.value for s in (RunStatus.PENDING, RunStatus.PLANNING, RunStatus.APPROVED, RunStatus.APPLYING)
)


# runs whose page offers a fresh plan of the flow.
PLAN_AGAIN = frozenset(
    s.value for s in (RunStatus.STALE, RunStatus.FAILED, RunStatus.EXPIRED, RunStatus.DRIFTED)
)


def _compact(value):
    return json.dumps(value, sort_keys=True, separators=(", ", ": ")) if value is not None else "-"


def _names(ops):
    """uid -> a readable name, for every object a plan creates or updates."""
    names = {}
    for op in ops:
        key = op.get("key") or (op.get("desired") or {}).get("key") or {}
        values = [v for v in key.values() if isinstance(v, str | int)]
        if op.get("uid") and values:
            names[op["uid"]] = "/".join(str(v) for v in values)
    return names


def _readable(value, names):
    """a value with the uids the plan defines replaced by their objects' names."""
    if isinstance(value, str):
        return names.get(value, value)
    if isinstance(value, list):
        return [_readable(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _readable(v, names) for k, v in value.items()}
    return value


def _changes(entry, names=None):
    names = names or {}
    return [
        {
            "field": c.get("field"),
            "from": _compact(_readable(c.get("from"), names)),
            "to": _compact(_readable(c.get("to"), names)),
        }
        for c in entry.get("changes") or ()
    ]


def drift_rows(report):
    """drift entries in category order, for review."""
    rows = []
    for category in ("changed", "missing", "extra"):
        for entry in (report or {}).get(category, ()):
            rows.append(
                {
                    "category": category,
                    "type_name": entry.get("type_name"),
                    "key": _compact(entry.get("key")),
                    "changes": _changes(entry),
                }
            )
    return rows


def group_ops(plan_text):
    """plan ops grouped for review: deletes, updates and creates, each by type."""
    if not plan_text:
        return []
    ops = json.loads(plan_text).get("ops", [])
    names = _names(ops)
    grouped = defaultdict(lambda: defaultdict(list))
    for op in ops:
        key = _readable(op.get("key") or (op.get("desired") or {}).get("key"), names)
        entry = {"key": _compact(key), "changes": _changes(op, names), "uid": op.get("uid")}
        grouped[op.get("op")][op.get("type_name")].append(entry)
    return [
        (kind, sorted(grouped[kind].items()))
        for kind in ("delete", "update", "create")
        if grouped.get(kind)
    ]


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
        reason = None
        if not user.has_perm("netbox_alembic.approve_run", instance):
            reason = _("You may not approve runs.")
        elif setting("require_distinct_approver") and instance.requested_by_id == user.pk:
            reason = _("Someone other than the requester approves this run.")
        elif actions.expired(instance):
            reason = _("This plan is too old to approve. Plan again.")
        waiting = instance.status == RunStatus.AWAITING_APPROVAL.value
        busy = instance.status in BUSY
        return {
            "busy": busy,
            "show_output": instance.status in (RunStatus.FAILED.value, RunStatus.STALE.value)
            or instance.status == RunStatus.APPLY_FAILED.value,
            "op_groups": group_ops(instance.plan),
            "drift_rows": drift_rows(instance.drift_report),
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
