"""end-to-end check of nautobot-alembic against a live nautobot and the real alembic.

runs inside the nautobot container (`dev/nautobot-e2e.sh`): it sets up its own
users, token, secrets group, git repository, backend and flow, then drives
everything through the rest api the way a user would, while the worker runs the
jobs. names carry a random suffix, so it can run against a nautobot with data.
"""

import json
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import (
    ApprovalWorkflowDefinition,
    ApprovalWorkflowStageDefinition,
    GitRepository,
    Secret,
    SecretsGroup,
    SecretsGroupAssociation,
)
from nautobot.users.models import Token
from nautobot_alembic.models import Backend, Flow, Run

API = "http://localhost:8080/api"
SUFFIX = secrets.token_hex(3)
REPO = Path(f"/opt/alembic-work/e2e-repo-{SUFFIX}")
LOCATION_TYPE = f"e2e-type-{SUFFIX}"
A, B = f"e2e-a-{SUFFIX}", f"e2e-b-{SUFFIX}"
failures = []
User = get_user_model()


UID = secrets.token_hex(4)


def inventory(status_b):
    uid = UID
    return f"""\
schema:
  types:
    extras.status:
      key:
        name: {{ type: string }}
      fields:
        name: {{ type: string }}
    dcim.locationtype:
      key:
        name: {{ type: string }}
      fields:
        name: {{ type: string }}
        content_types: {{ type: json }}
    dcim.location:
      key:
        name: {{ type: string }}
      fields:
        name: {{ type: string }}
        location_type: {{ type: ref, target: dcim.locationtype }}
        status: {{ type: ref, target: extras.status }}
objects:
  - uid: "{uid}-0000-4000-8000-{SUFFIX}000001"
    type: extras.status
    key: {{ name: Active }}
    attrs: {{ name: Active }}
  - uid: "{uid}-0000-4000-8000-{SUFFIX}000002"
    type: extras.status
    key: {{ name: Planned }}
    attrs: {{ name: Planned }}
  - uid: "{uid}-0000-4000-8000-{SUFFIX}000003"
    type: dcim.locationtype
    key: {{ name: {LOCATION_TYPE} }}
    attrs: {{ name: {LOCATION_TYPE}, content_types: ["dcim.device"] }}
  - uid: "{uid}-0000-4000-8000-{SUFFIX}000004"
    type: dcim.location
    key: {{ name: {A} }}
    attrs:
      name: {A}
      location_type: "{uid}-0000-4000-8000-{SUFFIX}000003"
      status: "{uid}-0000-4000-8000-{SUFFIX}000001"
  - uid: "{uid}-0000-4000-8000-{SUFFIX}000005"
    type: dcim.location
    key: {{ name: {B} }}
    attrs:
      name: {B}
      location_type: "{uid}-0000-4000-8000-{SUFFIX}000003"
      status: "{uid}-0000-4000-8000-{SUFFIX}00000{status_b}"
"""


def git(*args):
    subprocess.run(
        ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
    )


def commit(text, message):
    (REPO / "inventory.yaml").write_text(text)
    git("add", "-A")
    git("commit", "-q", "-m", message)


def token_for(user):
    return Token.objects.create(user=user).key


def call(token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Authorization", f"Token {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def wait(run_id, busy=("pending", "planning", "approved", "applying")):
    for _ in range(120):
        run = Run.objects.get(pk=run_id)
        if run.status not in busy:
            return run
        time.sleep(1)
    raise SystemExit(f"run {run_id} did not settle")


def plan(token, flow, kind="plan"):
    code, run = call(token, "POST", f"/plugins/alembic/flows/{flow.pk}/plan/", {"kind": kind})
    assert code == 202, (code, run)
    return wait(run["id"])


def location_status(name):
    code, found = call(admin_token, "GET", f"/dcim/locations/?name={name}&depth=1")
    return found["results"][0]["status"]["name"] if found and found.get("results") else None


admin_token = token_for(User.objects.get(username="admin"))
requester = User.objects.create_user(username=f"e2e-requester-{SUFFIX}", is_superuser=True)
approver = User.objects.create_user(username=f"e2e-approver-{SUFFIX}", is_superuser=True)
req, app = token_for(requester), token_for(approver)

secret = Secret.objects.create(
    name=f"e2e-token-{SUFFIX}",
    provider="environment-variable",
    parameters={"variable": "ALEMBIC_NAUTOBOT_TOKEN"},
)
group = SecretsGroup.objects.create(name=f"e2e-{SUFFIX}")
SecretsGroupAssociation.objects.create(
    secrets_group=group,
    secret=secret,
    access_type=SecretsGroupAccessTypeChoices.TYPE_HTTP,
    secret_type=SecretsGroupSecretTypeChoices.TYPE_TOKEN,
)

REPO.mkdir(parents=True)
git("init", "-q", "-b", "main")
commit(inventory("2"), "two locations, b planned")
repository = GitRepository.objects.create(
    name=f"e2e-{SUFFIX}", slug=f"e2e_{SUFFIX}", remote_url=f"file://{REPO}", branch="main"
)
target = Backend.objects.create(
    name=f"nautobot (e2e {SUFFIX})",
    kind="nautobot",
    config={"url": "http://nautobot:8080"},
    secrets_group=group,
)
flow = Flow.objects.create(
    name=f"e2e-{SUFFIX}", git_repository=repository, inventory="inventory.yaml", target=target
)

try:
    run = plan(req, flow)
    check("plan awaits approval", run.status == "awaiting_approval", run.error)
    check("plan creates the type and both locations", run.summary.get("create") == 3, run.summary)
    check("no workflow applies", run.associated_approval_workflows.count() == 0)

    code, _ = call(req, "POST", f"/plugins/alembic/runs/{run.pk}/approve/")
    check("requester cannot approve", code == 403, code)
    call(app, "POST", f"/plugins/alembic/runs/{run.pk}/approve/")
    run = wait(run.pk)
    check("approved run applies", run.status == "applied", run.error)
    check("the location exists", location_status(B) == "Planned", location_status(B))

    run = plan(req, flow)
    check("second plan has no changes", run.status == "no_changes", run.summary)

    commit(inventory("1"), "b goes active")
    run = plan(req, flow)
    check("changed inventory plans an update", run.summary.get("update") == 1, run.summary)
    code, found = call(admin_token, "GET", f"/dcim/locations/?name={B}")
    location_b = found["results"][0]["id"]
    retired = call(admin_token, "GET", "/extras/statuses/?name=Retired")[1]["results"][0]["id"]
    code, _ = call(admin_token, "PATCH", f"/dcim/locations/{location_b}/", {"status": retired})
    check("target edited behind the plan", code == 200, code)
    call(app, "POST", f"/plugins/alembic/runs/{run.pk}/approve/")
    run = wait(run.pk)
    check("target change makes the plan stale", run.status == "stale", run.error)
    check("stale plan wrote nothing", location_status(B) == "Retired", location_status(B))

    run = plan(req, flow, kind="drift")
    check("drift report sees the drift", run.status == "drifted", run.error)

    # an approval workflow for runs: its one stage decides, not the app's button.
    definition = ApprovalWorkflowDefinition.objects.create(
        name=f"e2e-{SUFFIX}",
        model_content_type=ContentType.objects.get_for_model(Run),
        model_constraints={"flow__name": flow.name},
        weight=100,
    )
    from django.contrib.auth.models import Group

    approvers = Group.objects.create(name=f"e2e-approvers-{SUFFIX}")
    approver.groups.add(approvers)
    ApprovalWorkflowStageDefinition.objects.create(
        approval_workflow_definition=definition,
        name="review",
        sequence=1,
        min_approvers=1,
        approver_group=approvers,
    )
    run = plan(req, flow)
    workflow = run.associated_approval_workflows.first()
    check("a matching definition starts a workflow", workflow is not None)
    code, _ = call(app, "POST", f"/plugins/alembic/runs/{run.pk}/approve/")
    check("the app's approve defers to the workflow", code == 409, code)
    stage = workflow.approval_workflow_stages.first()
    code, body = call(app, "POST", f"/extras/approval-workflow-stages/{stage.pk}/approve/", {})
    check("the workflow stage approves", code in (200, 201), (code, body))
    run = wait(run.pk)
    check("the workflow's approval applies the run", run.status == "applied", run.error)
    check("the location converged", location_status(B) == "Active", location_status(B))
    check("the approver is recorded", run.decided_by_id == approver.pk, run.decided_by)
    definition.delete()

    # nautobot as the source: import what the flow applied, plan it back.
    mirror = Flow.objects.create(
        name=f"e2e-mirror-{SUFFIX}",
        git_repository=repository,
        inventory="inventory.yaml",
        source=target,
        target=target,
    )
    run = plan(req, mirror)
    check("an import plans from what it imported", run.status == "no_changes", run.error)
    check("the import is the run's input", run.input_entry == "inventory.json", run.input_entry)
finally:
    subprocess.run(["rm", "-rf", str(REPO)], check=False)

print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
if failures:
    sys.exit(1)
