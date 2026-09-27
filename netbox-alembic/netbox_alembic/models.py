from alembic_runner import ACTIVE, CONFIG_KEYS, KINDS, RunStatus
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import ChangeLoggedModel, NetBoxModel
from netbox.models.features import JobsMixin
from utilities.choices import ChoiceSet

from .settings import setting

KIND_LABELS = {
    "netbox": "NetBox",
    "nautobot": "Nautobot",
    "infrahub": "Infrahub",
    "peeringdb": "PeeringDB",
    "external": "External adapter",
}


class BackendKindChoices(ChoiceSet):
    CHOICES = [(kind, KIND_LABELS[kind]) for kind in KINDS]


class RunKindChoices(ChoiceSet):
    PLAN = "plan"
    DRIFT = "drift"
    CHOICES = [(PLAN, _("Plan"), "blue"), (DRIFT, _("Drift report"), "gray")]


_STATUS_COLORS = {
    RunStatus.PENDING: "gray",
    RunStatus.PLANNING: "cyan",
    RunStatus.NO_CHANGES: "green",
    RunStatus.DRIFTED: "orange",
    RunStatus.FAILED: "red",
    RunStatus.AWAITING_APPROVAL: "yellow",
    RunStatus.REJECTED: "gray",
    RunStatus.SUPERSEDED: "gray",
    RunStatus.EXPIRED: "gray",
    RunStatus.APPROVED: "blue",
    RunStatus.APPLYING: "cyan",
    RunStatus.APPLIED: "green",
    RunStatus.STALE: "orange",
    RunStatus.APPLY_FAILED: "red",
}


class RunStatusChoices(ChoiceSet):
    CHOICES = [
        (s.value, s.value.replace("_", " ").capitalize(), _STATUS_COLORS[s]) for s in RunStatus
    ]


class Backend(NetBoxModel):
    """a system alembic can plan against."""

    name = models.CharField(verbose_name=_("name"), max_length=100, unique=True)
    kind = models.CharField(verbose_name=_("kind"), max_length=30, choices=BackendKindChoices)
    config = models.JSONField(
        verbose_name=_("config"),
        default=dict,
        blank=True,
        help_text=_("The non-secret part of an alembic backend config, without the backend key."),
    )
    credential = models.CharField(
        verbose_name=_("credential"),
        max_length=100,
        blank=True,
        help_text=_(
            "Name of a credential in plugin settings. The secret stays out of the database."
        ),
    )
    external_adapter = models.CharField(
        verbose_name=_("external adapter"),
        max_length=100,
        blank=True,
        help_text=_("Name of an adapter binary in plugin settings, for external backends."),
    )
    description = models.CharField(verbose_name=_("description"), max_length=200, blank=True)
    comments = models.TextField(verbose_name=_("comments"), blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("backend")
        verbose_name_plural = _("backends")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_alembic:backend", args=[self.pk])

    def clean(self):
        super().clean()
        if not isinstance(self.config, dict):
            raise ValidationError({"config": _("Config must be a mapping.")})
        unknown = set(self.config) - CONFIG_KEYS.get(self.kind, frozenset())
        if unknown:
            raise ValidationError(
                {
                    "config": _("A {kind} backend cannot set {keys}; it takes {allowed}").format(
                        kind=self.kind,
                        keys=", ".join(sorted(unknown)),
                        allowed=", ".join(sorted(CONFIG_KEYS.get(self.kind, ()))),
                    )
                }
            )
        if self.kind == "external":
            if self.external_adapter not in setting("external_adapters"):
                raise ValidationError(
                    {"external_adapter": _("No adapter by that name in plugin settings.")}
                )
        elif self.external_adapter:
            raise ValidationError(
                {"external_adapter": _("Only external backends name an adapter.")}
            )
        if self.credential and self.credential not in setting("credentials"):
            raise ValidationError({"credential": _("No credential by that name in settings.")})


class Flow(NetBoxModel):
    """a reusable run definition: an inventory in a data source, planned against a target."""

    name = models.CharField(verbose_name=_("name"), max_length=100, unique=True)
    data_source = models.ForeignKey(
        to="core.DataSource",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("data source"),
    )
    root = models.CharField(
        verbose_name=_("root"),
        max_length=500,
        blank=True,
        help_text=_(
            "Directory inside the data source whose files a run snapshots. Empty means all."
        ),
    )
    inventory = models.CharField(
        verbose_name=_("inventory"),
        max_length=500,
        help_text=_("Path of the inventory file, relative to the root."),
    )
    target = models.ForeignKey(
        to=Backend,
        on_delete=models.PROTECT,
        related_name="flows",
        verbose_name=_("target"),
    )
    allow_delete = models.BooleanField(verbose_name=_("allow delete"), default=False)
    no_adopt = models.BooleanField(
        verbose_name=_("no adopt"),
        default=False,
        help_text=_("Do not bind existing backend objects to declared ones by key."),
    )
    state_key = models.CharField(
        verbose_name=_("state key"),
        max_length=100,
        blank=True,
        help_text=_("Workspace for PostgreSQL state. Defaults to flow-7 for flow 7."),
    )
    description = models.CharField(verbose_name=_("description"), max_length=200, blank=True)
    comments = models.TextField(verbose_name=_("comments"), blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("flow")
        verbose_name_plural = _("flows")

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_alembic:flow", args=[self.pk])

    def clean(self):
        super().clean()
        for field in ("root", "inventory"):
            value = getattr(self, field).strip("/")
            if value.startswith("/") or ".." in value.split("/"):
                raise ValidationError({field: _("Must be a relative path inside the data source.")})
            setattr(self, field, value)
        if not self.inventory:
            raise ValidationError({"inventory": _("Name the inventory file.")})

    @property
    def root_prefix(self):
        return f"{self.root}/" if self.root else ""


class Run(JobsMixin, ChangeLoggedModel):
    """one plan of a flow, and at most one apply of it."""

    flow = models.ForeignKey(
        to=Flow, on_delete=models.PROTECT, related_name="runs", verbose_name=_("flow")
    )
    kind = models.CharField(
        verbose_name=_("kind"), max_length=10, choices=RunKindChoices, default=RunKindChoices.PLAN
    )
    status = models.CharField(
        verbose_name=_("status"),
        max_length=30,
        choices=RunStatusChoices,
        default=RunStatus.PENDING.value,
    )
    requested_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("requested by"),
    )
    decided_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(verbose_name=_("decided at"), null=True, blank=True)
    planned_at = models.DateTimeField(verbose_name=_("planned at"), null=True, blank=True)
    apply_started_at = models.DateTimeField(
        verbose_name=_("apply started at"), null=True, blank=True
    )
    finished_at = models.DateTimeField(verbose_name=_("finished at"), null=True, blank=True)

    input = models.JSONField(verbose_name=_("input"), default=dict, blank=True, editable=False)
    input_sha256 = models.CharField(verbose_name=_("input SHA-256"), max_length=64, blank=True)
    # the plan is kept as the bytes alembic wrote: the approval covers their hash,
    # and a json column would not give the same bytes back.
    plan = models.TextField(verbose_name=_("plan"), blank=True, editable=False)
    plan_sha256 = models.CharField(verbose_name=_("plan SHA-256"), max_length=64, blank=True)
    approved_sha256 = models.CharField(
        verbose_name=_("approved SHA-256"), max_length=64, blank=True
    )
    summary = models.JSONField(verbose_name=_("summary"), default=dict, blank=True)
    drift_report = models.JSONField(verbose_name=_("drift report"), null=True, blank=True)
    apply_report = models.JSONField(verbose_name=_("apply report"), null=True, blank=True)
    alembic_version = models.CharField(verbose_name=_("alembic version"), max_length=30, blank=True)
    error = models.TextField(verbose_name=_("error"), blank=True)
    output = models.TextField(verbose_name=_("output"), blank=True, editable=False)

    class Meta:
        ordering = ("-created",)
        verbose_name = _("run")
        verbose_name_plural = _("runs")
        permissions = [("approve_run", "approve or reject a run")]
        constraints = [
            models.UniqueConstraint(
                fields=["flow"],
                condition=Q(status__in=sorted(s.value for s in ACTIVE)),
                name="netbox_alembic_run_one_active_per_flow",
                violation_error_message=_("This flow already has a run in progress."),
            ),
        ]

    def __str__(self):
        return f"{self.flow} #{self.pk}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_alembic:run", args=[self.pk])

    def get_status_color(self):
        return RunStatusChoices.colors.get(self.status)

    def get_kind_color(self):
        return RunKindChoices.colors.get(self.kind)

    @property
    def status_enum(self):
        return RunStatus(self.status)
