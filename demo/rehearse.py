#!/usr/bin/env python3
"""walks the demo through the rest api and checks each step, printing what the
ui would show. run it on a fresh `demo/setup.sh`; it leaves the demo at its end
state, so run setup.sh again before showing it to anyone."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = f"http://localhost:{os.environ.get('NETBOX_PORT', '8020')}/api"
failures = []


def manage_token(username):
    code = (
        "from users.models import Token, User\n"
        f"t = Token(user=User.objects.get(username='{username}'), write_enabled=True)\n"
        "t.save(); print(f'TOKEN nbt_{t.key}.{t.token}')\n"
    )
    out = subprocess.run(
        [
            f"{HERE}/compose",
            "exec",
            "-T",
            "netbox",
            "/opt/netbox/venv/bin/python",
            "/opt/netbox/netbox/manage.py",
            "shell",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("TOKEN "))


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
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def wait(token, run_id):
    for _ in range(120):
        _, run = call(token, "GET", f"/plugins/alembic/runs/{run_id}/")
        if run["status"]["value"] not in ("pending", "planning", "approved", "applying"):
            return run
        time.sleep(1)
    sys.exit(f"run {run_id} did not settle")


def start(token, kind="plan"):
    _, flow = call(token, "GET", "/plugins/alembic/flows/?name=fabric")
    code, run = call(
        token, "POST", f"/plugins/alembic/flows/{flow['results'][0]['id']}/plan/", {"kind": kind}
    )
    assert code == 202, (code, run)
    return wait(token, run["id"])


def output_of(run_id):
    code = f"from netbox_alembic.models import Run\nprint(Run.objects.get(pk={run_id}).output)\n"
    out = subprocess.run(
        [
            f"{HERE}/compose",
            "exec",
            "-T",
            "netbox",
            "/opt/netbox/venv/bin/python",
            "/opt/netbox/netbox/manage.py",
            "shell",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    noise = ("loaded config", "objects imported automatically")
    return "\n".join(ln for ln in out.splitlines() if not any(n in ln for n in noise)).strip()


def device(token, name):
    _, found = call(token, "GET", f"/dcim/devices/?name={name}")
    return found["results"][0]


engineer, reviewer = manage_token("engineer"), manage_token("reviewer")

print("1. the engineer plans the fabric")
run = start(engineer)
check("awaiting approval", run["status"]["value"] == "awaiting_approval", run["error"])
print(output_of(run["id"]))

print("2. approval")
code, _ = call(engineer, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
check("the engineer cannot approve their own plan", code == 403, code)
call(reviewer, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
run = wait(reviewer, run["id"])
check("the reviewer's approval applies it", run["status"]["value"] == "applied", run["error"])
print(output_of(run["id"]))

print("3. a change arrives through git")
subprocess.run([f"{HERE}/commit-change.sh"], check=True)
run = start(engineer)
check("one update", run["summary"].get("update") == 1, run["summary"])
print(output_of(run["id"]))

print("4. someone edits netbox by hand before the review")
leaf = device(engineer, "ber1-leaf02")
code, _ = call(engineer, "PATCH", f"/dcim/devices/{leaf['id']}/", {"status": "offline"})
check("hand edit", code == 200, code)
call(reviewer, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
run = wait(reviewer, run["id"])
check("the approved plan is stale", run["status"]["value"] == "stale", run["error"])
check("nothing was written", device(engineer, "ber1-leaf02")["status"]["value"] == "offline")
print(output_of(run["id"]))

print("5. drift")
run = start(engineer, kind="drift")
check(
    "the hand edit is drift",
    run["summary"] == {"changed": 1, "missing": 0, "extra": 0},
    run["summary"],
)
print(output_of(run["id"]))

print("6. plan again, approve, converge")
run = start(engineer)
call(reviewer, "POST", f"/plugins/alembic/runs/{run['id']}/approve/")
run = wait(reviewer, run["id"])
check("applied", run["status"]["value"] == "applied", run["error"])
check("leaf02 is live", device(engineer, "ber1-leaf02")["status"]["value"] == "active")
run = start(engineer, kind="drift")
check("no drift left", run["status"]["value"] == "no_changes", run["summary"])

print(f"\n{len(failures)} failed" if failures else "\nthe demo works")
sys.exit(1 if failures else 0)
