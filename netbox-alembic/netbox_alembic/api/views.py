from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from netbox.api.authentication import TokenPermissions
from netbox.api.viewsets import NetBoxModelViewSet, NetBoxReadOnlyModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .. import actions, filtersets
from ..models import Backend, Flow, Run, RunKindChoices
from . import serializers


class ActionPermissions(TokenPermissions):
    """a run action needs to see its object and a token that may write. whether
    the user may start, approve or resume is the action's own check: netbox's
    default would demand `add` on the flow or run for any POST."""

    perms_map = {**TokenPermissions.perms_map, "POST": ["%(app_label)s.view_%(model_name)s"]}


def _act(request, fn, *args):
    try:
        return fn(*args)
    except DjangoPermissionDenied as e:
        raise PermissionDenied(str(e)) from e
    except ValidationError as e:
        return Response({"detail": " ".join(e.messages)}, status=status.HTTP_409_CONFLICT)


class BackendViewSet(NetBoxModelViewSet):
    queryset = Backend.objects.all()
    serializer_class = serializers.BackendSerializer
    filterset_class = filtersets.BackendFilterSet


class FlowViewSet(NetBoxModelViewSet):
    queryset = Flow.objects.select_related("data_source", "source", "target")
    serializer_class = serializers.FlowSerializer
    filterset_class = filtersets.FlowFilterSet

    @action(detail=True, methods=["post"], permission_classes=[ActionPermissions])
    def plan(self, request, pk):
        """start a plan (or, with `{"kind": "drift"}`, a drift report) for this flow."""
        if not request.user.has_perm("netbox_alembic.add_run"):
            raise PermissionDenied("you may not start runs")
        flow = get_object_or_404(Flow.objects.restrict(request.user, "view"), pk=pk)
        kind = request.data.get("kind", RunKindChoices.PLAN)
        if kind not in (RunKindChoices.PLAN, RunKindChoices.DRIFT):
            return Response({"detail": "unknown run kind"}, status=status.HTTP_400_BAD_REQUEST)
        run = _act(request, actions.request_run, flow, request.user, kind)
        if isinstance(run, Response):
            return run
        data = serializers.RunSerializer(run, context={"request": request}).data
        return Response(data, status=status.HTTP_202_ACCEPTED)


class RunViewSet(NetBoxReadOnlyModelViewSet):
    queryset = Run.objects.select_related("flow", "requested_by", "decided_by")
    serializer_class = serializers.RunSerializer
    filterset_class = filtersets.RunFilterSet

    def _decide(self, request, pk, fn, code=status.HTTP_200_OK):
        # the viewset restricts its queryset by the method (POST reads as "add");
        # an action only needs the run to be visible.
        run = get_object_or_404(Run.objects.restrict(request.user, "view"), pk=pk)
        result = _act(request, fn, run, request.user)
        if isinstance(result, Response):
            return result
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
