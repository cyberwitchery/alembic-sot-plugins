"""end-to-end check of netbox-alembic against a live netbox and the real alembic.

runs inside the netbox container (`dev/manage shell < dev/e2e.py`): it sets up its
own users, tokens, data source, backend and flow, then drives everything through
the rest api the way a user would, while the worker runs the jobs. every name
carries a random suffix, so it can run against a netbox that already has data.
"""

import json
import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request

from core.models import DataSource
from netbox_alembic.models import Backend, Flow
from users.models import Token, User

API = "http://localhost:8080/api"
SUFFIX = secrets.token_hex(3)
INVENTORY_DIR = f"/opt/alembic-work/e2e-{SUFFIX}"
SLUG_A, SLUG_B = f"e2e-a-{SUFFIX}", f"e2e-b-{SUFFIX}"
failures = []


def inventory(status_b):
    return f"""\
schema:
  types:
    dcim.site:
      key:
        slug: {{ type: slug }}
      fields:
        name: {{ type: string }}
        slug: {{ type: slug }}
        status: {{ type: string }}
objects:
  - uid: "{secrets.token_hex(4)}-0000-4000-8000-{SUFFIX}000001"
    type: dcim.site
    key: {{ slug: {SLUG_A} }}
    attrs: {{ name: A {SUFFIX}, slug: {SLUG_A}, status: active }}
  - uid: "{secrets.token_hex(4)}-0000-4000-8000-{SUFFIX}000002"
    type: dcim.site
    key: {{ slug: {SLUG_B} }}
    attrs: {{ name: B {SUFFIX}, slug: {SLUG_B}, status: {status_b} }}
"""


def token_for(user):
    token = Token(user=user, write_enabled=True, description=f"e2e {SUFFIX}")
    token.save()
    return f"nbt_{token.key}.{token.token}"


def call(token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def wait(token, run_id, busy=("pending", "planning", "approved", "applying")):
    for _ in range(90):
        _, run = call(token, "GET", f"/plugins/alembic/runs/{run_id}/")
        if run["status"]["value"] not in busy:
            return run
        time.sleep(1)
    raise SystemExit(f"run {run_id} did not settle")


def plan(token, flow_id, kind="plan"):
    code, run = call(token, "POST", f"/plugins/alembic/flows/{flow_id}/plan/", {"kind": kind})
    assert code == 202, (code, run)
    return wait(token, run["id"])


requester = User.objects.create_user(username=f"e2e-requester-{SUFFIX}", is_superuser=True)
approver = User.objects.create_user(username=f"e2e-approver-{SUFFIX}", is_superuser=True)
req, app = token_for(requester), token_for(approver)

os.makedirs(INVENTORY_DIR)
with open(f"{INVENTORY_DIR}/inventory.yaml", "w") as f:
    f.write(inventory("planned"))
source = DataSource.objects.create(
    name=f"e2e-{SUFFIX}", type="local", source_url=f"file://{INVENTORY_DIR}", parameters={}
)
target = Backend.objects.get_or_create(
    name="netbox (e2e)",
    defaults={"kind": "netbox", "config": {"url": "http://netbox:8080"}, "credential": "netbox"},
)[0]
flow = Flow.objects.create(
    name=f"e2e-{SUFFIX}", data_source=source, inventory="inventory.yaml", target=target
)

try:
    run = plan(req, flow.pk)
    check("plan awaits approval", run["status"]["value"] == "awaiting_approval", run["error"])
    check("plan creates both sites", run["summary"].get("create") == 2, run["summary"])

    code, _ = call(req, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
    check("requester cannot approve", code == 403, code)

    code, _ = call(app, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
    run = wait(app, run["id"])
    check("approved run applies", run["status"]["value"] == "applied", run["error"])
    _, sites = call(app, "GET", f"/dcim/sites/?slug={SLUG_A}&slug={SLUG_B}")
    check("sites exist", sites["count"] == 2, sites["count"])

    run = plan(req, flow.pk)
    check("second plan has no changes", run["status"]["value"] == "no_changes", run["summary"])

    with open(f"{INVENTORY_DIR}/inventory.yaml", "w") as f:
        f.write(inventory("active"))
    run = plan(req, flow.pk)
    check("changed inventory plans an update", run["summary"].get("update") == 1, run["summary"])
    site_b = sites["results"][[s["slug"] for s in sites["results"]].index(SLUG_B)]
    edit = {"name": f"edited behind the plan {SUFFIX}"}
    code, _ = call(app, "PATCH", f"/dcim/sites/{site_b['id']}/", edit)
    check("target edited behind the plan", code == 200, code)
    call(app, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
    run = wait(app, run["id"])
    check("target change makes the plan stale", run["status"]["value"] == "stale", run["error"])
    _, site = call(app, "GET", f"/dcim/sites/{site_b['id']}/")
    check("stale plan wrote nothing", site["status"]["value"] == "planned", site["status"])

    run = plan(req, flow.pk, kind="drift")
    check("drift report sees the drift", run["status"]["value"] == "drifted", run["error"])
    check("drift report lists the changed site", run["summary"].get("changed") == 1, run["summary"])
finally:
    shutil.rmtree(INVENTORY_DIR, ignore_errors=True)

print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
if failures:
    sys.exit(1)
