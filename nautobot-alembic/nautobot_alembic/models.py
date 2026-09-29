from alembic_runner import ACTIVE, CONFIG_KEYS, KINDS, RunStatus
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from nautobot.apps.choices import ChoiceSet
from nautobot.apps.constants import CHARFIELD_MAX_LENGTH
from nautobot.apps.models import (
    ApprovableModelMixin,
    BaseModel,
    ChangeLoggedModel,
    PrimaryModel,
    extras_features,
)

from .settings import setting

KIND_LABELS = {
    "netbox": "NetBox",
    "nautobot": "Nautobot",
    "infrahub": "Infrahub",
    "peeringdb": "PeeringDB",
    "external": "External adapter",
}


class BackendKindChoices(ChoiceSet):
    CHOICES = tuple((kind, KIND_LABELS[kind]) for kind in KINDS)


class RunKindChoices(ChoiceSet):
    PLAN = "plan"
    DRIFT = "drift"
    CHOICES = ((PLAN, "Plan"), (DRIFT, "Drift report"))


class RunStatusChoices(ChoiceSet):
    CHOICES = tuple((s.value, s.value.replace("_", " ").capitalize()) for s in RunStatus)

    # bootstrap background colours, as nautobot's own status badges use them.
    CSS_CLASSES = {
        RunStatus.PENDING.value: "secondary",
        RunStatus.PLANNING.value: "info",
        RunStatus.NO_CHANGES.value: "success",
        RunStatus.DRIFTED.value: "warning",
        RunStatus.FAILED.value: "danger",
        RunStatus.AWAITING_APPROVAL.value: "warning",
        RunStatus.REJECTED.value: "secondary",
        RunStatus.SUPERSEDED.value: "secondary",
        RunStatus.EXPIRED.value: "secondary",
        RunStatus.APPROVED.value: "primary",
        RunStatus.APPLYING.value: "info",
        RunStatus.APPLIED.value: "success",
        RunStatus.STALE.value: "warning",
        RunStatus.APPLY_FAILED.value: "danger",
    }


def _relative(value, field):
    value = value.strip("/")
    if value.startswith("/") or ".." in value.split("/"):
        raise ValidationError({field: "Must be a relative path inside the repository."})
    return value


@extras_features("custom_links", "custom_validators", "export_templates", "graphql", "webhooks")
class Backend(PrimaryModel):
    """a system alembic can plan against, or import from."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True)
    kind = models.CharField(max_length=30, choices=BackendKindChoices)
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="The non-secret part of an alembic backend config, without the backend key.",
    )
    secrets_group = models.ForeignKey(
        to="extras.SecretsGroup",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Holds the backend's token as an HTTP(S) access, token secret.",
    )
    external_adapter = models.CharField(
        max_length=CHARFIELD_MAX_LENGTH,
        blank=True,
        help_text="Name of an adapter binary in app settings, for external backends.",
    )
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be a mapping."})
        allowed = CONFIG_KEYS.get(self.kind, frozenset())
        unknown = set(self.config) - allowed
        if unknown:
            raise ValidationError(
                {
                    "config": f"A {self.kind} backend cannot set {', '.join(sorted(unknown))}; "
                    f"it takes {', '.join(sorted(allowed))}."
                }
            )
        if self.kind == "external":
            if self.external_adapter not in setting("external_adapters"):
                raise ValidationError(
                    {"external_adapter": "No adapter by that name in app settings."}
                )
        elif self.external_adapter:
            raise ValidationError({"external_adapter": "Only external backends name an adapter."})


@extras_features("custom_links", "custom_validators", "export_templates", "graphql", "webhooks")
class Flow(PrimaryModel):
    """a reusable run definition: an inventory in a git repository, planned
    against a target, optionally imported from a source and mapped on the way."""

    name = models.CharField(max_length=CHARFIELD_MAX_LENGTH, unique=True)
    git_repository = models.ForeignKey(
        to="extras.GitRepository", on_delete=models.PROTECT, related_name="+"
    )
    root = models.CharField(
        max_length=500,
        blank=True,
        help_text="Directory inside the repository whose files a run snapshots. Empty means all.",
    )
    inventory = models.CharField(
        max_length=500, help_text="Path of the inventory file, relative to the root."
    )
    source = models.ForeignKey(
        to=Backend,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_flows",
        help_text="Import the inventory from this backend. The inventory file then only "
        "selects the types to import.",
    )
    map_spec = models.CharField(
        max_length=500,
        blank=True,
        help_text="Map spec that reshapes the inventory for the target, relative to the root.",
    )
    target = models.ForeignKey(to=Backend, on_delete=models.PROTECT, related_name="flows")
    allow_delete = models.BooleanField(default=False)
    no_adopt = models.BooleanField(
        default=False, help_text="Do not bind existing backend objects to declared ones by key."
    )
    state_key = models.CharField(
        max_length=100,
        blank=True,
        help_text="Workspace for PostgreSQL state. Defaults to flow-<id>.",
    )
    description = models.CharField(max_length=CHARFIELD_MAX_LENGTH, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.root = _relative(self.root, "root")
        self.inventory = _relative(self.inventory, "inventory")
        self.map_spec = _relative(self.map_spec, "map_spec")
        if not self.inventory:
            raise ValidationError({"inventory": "Name the inventory file."})


@extras_features("graphql", "webhooks")
class Run(ApprovableModelMixin, ChangeLoggedModel, BaseModel):
    """one plan of a flow, and at most one apply of it."""

    flow = models.ForeignKey(to=Flow, on_delete=models.PROTECT, related_name="runs")
    kind = models.CharField(max_length=10, choices=RunKindChoices, default=RunKindChoices.PLAN)
    status = models.CharField(
        max_length=30, choices=RunStatusChoices, default=RunStatus.PENDING.value
    )
    # the first user field: nautobot's approval workflow names it as the requester.
    requested_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    planned_at = models.DateTimeField(null=True, blank=True)
    apply_started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    input = models.JSONField(default=dict, blank=True, editable=False)
    input_entry = models.CharField(max_length=500, blank=True, editable=False)
    input_sha256 = models.CharField(verbose_name="input SHA-256", max_length=64, blank=True)
    # the plan is kept as the bytes alembic wrote: the approval covers their hash.
    plan = models.TextField(blank=True, editable=False)
    plan_sha256 = models.CharField(verbose_name="plan SHA-256", max_length=64, blank=True)
    approved_sha256 = models.CharField(verbose_name="approved SHA-256", max_length=64, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    drift_report = models.JSONField(null=True, blank=True)
    apply_report = models.JSONField(null=True, blank=True)
    alembic_version = models.CharField(max_length=30, blank=True)
    error = models.TextField(blank=True)
    output = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ("-created",)
        permissions = [("approve_run", "Approve or reject a run")]
        constraints = [
            models.UniqueConstraint(
                fields=["flow"],
                condition=Q(status__in=sorted(s.value for s in ACTIVE)),
                name="nautobot_alembic_run_one_active_per_flow",
                violation_error_message="This flow already has a run in progress.",
            ),
        ]

    def __str__(self):
        return f"{self.flow} #{str(self.pk)[:8]}"

    def get_status_class(self):
        return RunStatusChoices.CSS_CLASSES.get(self.status, "secondary")

    def get_absolute_url(self, api=False):
        if api:
            return reverse("plugins-api:nautobot_alembic-api:run-detail", args=[self.pk])
        return reverse("plugins:nautobot_alembic:run", args=[self.pk])

    # approval workflows (nautobot.extras.models.approvals). the run's own state
    # moves through `actions`, which also covers runs no workflow applies to.

    def on_workflow_initiated(self, approval_workflow):
        pass

    def on_workflow_approved(self, approval_workflow):
        from .actions import workflow_approved

        workflow_approved(self, approval_workflow)

    def on_workflow_denied(self, approval_workflow):
        from .actions import workflow_denied

        workflow_denied(self, approval_workflow)

    def on_workflow_canceled(self, approval_workflow):
        from .actions import workflow_denied

        workflow_denied(self, approval_workflow)
