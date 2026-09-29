from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from nautobot.apps.api import NautobotModelViewSet, ReadOnlyModelViewSet
from nautobot.core.api.authentication import TokenPermissions
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .. import actions, filters
from ..models import Backend, Flow, Run, RunKindChoices
from . import serializers


class ActionPermissions(TokenPermissions):
    """a run action needs to see its object and a token that may write. whether
    the user may start, approve or resume is the action's own check: nautobot's
    default would demand `add` on the flow or run for any POST."""

    perms_map = {**TokenPermissions.perms_map, "POST": ["%(app_label)s.view_%(model_name)s"]}


def _act(fn, *args):
    try:
        return fn(*args), None
    except DjangoPermissionDenied as e:
        raise PermissionDenied(str(e)) from e
    except ValidationError as e:
        return None, Response({"detail": " ".join(e.messages)}, status=status.HTTP_409_CONFLICT)


class BackendViewSet(NautobotModelViewSet):
    queryset = Backend.objects.select_related("secrets_group")
    serializer_class = serializers.BackendSerializer
    filterset_class = filters.BackendFilterSet


class FlowViewSet(NautobotModelViewSet):
    queryset = Flow.objects.select_related("git_repository", "source", "target")
    serializer_class = serializers.FlowSerializer
    filterset_class = filters.FlowFilterSet

    @action(detail=True, methods=["post"], permission_classes=[ActionPermissions])
    def plan(self, request, pk):
        """start a plan (or, with `{"kind": "drift"}`, a drift report) for this flow."""
        if not request.user.has_perm("nautobot_alembic.add_run"):
            raise PermissionDenied("You may not start runs.")
        flow = get_object_or_404(Flow.objects.restrict(request.user, "view"), pk=pk)
        kind = request.data.get("kind", RunKindChoices.PLAN)
        if kind not in (RunKindChoices.PLAN, RunKindChoices.DRIFT):
            return Response({"detail": "Unknown run kind."}, status=status.HTTP_400_BAD_REQUEST)
        run, refused = _act(actions.request_run, flow, request.user, kind)
        if refused:
            return refused
        data = serializers.RunSerializer(run, context={"request": request}).data
        return Response(data, status=status.HTTP_202_ACCEPTED)


class RunViewSet(ReadOnlyModelViewSet):
    queryset = Run.objects.select_related("flow", "requested_by", "decided_by")
    serializer_class = serializers.RunSerializer
    filterset_class = filters.RunFilterSet

    def _decide(self, request, pk, fn, code=status.HTTP_200_OK):
        # the viewset restricts its queryset by the method (POST reads as "add");
        # an action only needs the run to be visible.
        run = get_object_or_404(Run.objects.restrict(request.user, "view"), pk=pk)
        result, refused = _act(fn, run, request.user)
        if refused:
            return refused
        data = serializers.RunSerializer(result, context={"request": request}).data
        return Response(data, status=code)

    @action(detail=True, methods=["post"], permission_classes=[ActionPermissions])
    def approve(self, request, pk):
        return self._decide(request, pk, actions.approve, status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], permission_classes=[ActionPermissions])
    def reject(self, request, pk):
        return self._decide(request, pk, actions.reject)

    @action(detail=True, methods=["post"], permission_classes=[ActionPermissions])
    def resume(self, request, pk):
        return self._decide(request, pk, actions.resume, status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="plan")
    def plan_file(self, request, pk):
        """the plan exactly as alembic wrote it."""
        run = get_object_or_404(self.get_queryset(), pk=pk)
        return HttpResponse(run.plan, content_type="application/json")
