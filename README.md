# alembic-sot-plugins

plugins that run [alembic](https://github.com/cyberwitchery/alembic) from inside
a source of truth: plan in the background, review the plan in the ui, approve,
apply.

- `alembic-runner`: the host-independent core. runs the `alembic` cli, parses
  what it writes, owns the run state machine.
- `netbox-alembic`: the netbox plugin, netbox 4.6 or later.
- `nautobot-alembic`: the nautobot app. not written yet.

## how a run goes

a **backend** is something alembic plans against: this netbox, or another
system. a **flow** names an inventory in a netbox data source and a target
backend. a **run** is one plan of a flow and at most one apply of it.

```text
plan (background job: sync the data source, snapshot the files, alembic plan)
  → awaiting approval
  → approve (someone other than the requester, with the approve permission)
  → stale check (alembic plan --dry-run against the snapshot)
  → apply of the approved plan bytes
```

- plan never writes to a backend. schema work shows up in the plan and happens
  at apply.
- apply writes exactly the plan that was approved: the job checks the stored
  plan against the hash recorded at approval.
- if the target changed between plan and apply, the run ends `stale` and
  nothing is written. plan again.
- a failed apply can be resumed; alembic's journal skips what already landed.
- a newer plan supersedes the flow's waiting plan and takes away a pending
  resume. a flow has one run in progress at a time, and a target one apply.

a drift run (`alembic plan --report`) records how the target differs from the
inventory and never goes to approval.

## install

on every netbox host, and on the worker that runs the jobs:

```sh
pip install netbox-alembic
```

put the `alembic` binary on the worker (releases attach static binaries), add
the plugin to `configuration.py`, run `manage.py migrate`, and start a worker for
the plugin's queue. the default worker does not listen on it:

```sh
manage.py rqworker netbox_alembic.alembic
```

run one worker for that queue, with `work_root` on persistent disk. alembic keeps
its apply journal in the flow's working directory, so a resume has to run where
the interrupted apply ran.

## settings

```python
PLUGINS = ["netbox_alembic"]
PLUGINS_CONFIG = {
    "netbox_alembic": {
        "alembic_path": "/usr/local/bin/alembic",
        "work_root": "/var/lib/netbox-alembic",
        # identity state. "local" keeps it in each flow's directory.
        "state": {"backend": "postgres", "postgres_url": "postgres://alembic@db/alembic"},
        # this netbox, as the worker reaches it, and the credential to use.
        "self_url": "http://netbox:8080",
        "self_credential": "self",
        # credentials by name: each value names worker environment variables.
        # "token" feeds the adapter's own token variable (NETBOX_TOKEN, ...);
        # "env" sets adapter variables for external adapters.
        "credentials": {
            "self": {"token": "ALEMBIC_SELF_TOKEN"},
            "lab-nautobot": {"token": "LAB_NAUTOBOT_TOKEN"},
        },
        # external adapter binaries by name. a backend names one, never a path.
        "external_adapters": {"mybackend": "/usr/local/bin/alembic-adapter-mybackend"},
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
    },
}
```

secrets never enter the database. a backend stores the name of a credential,
and the worker reads the value from its environment at run time. alembic runs
with an environment built for the run, so the worker's own secrets (django
`SECRET_KEY`, database passwords) do not reach it.

a backend's config holds only connection keys (`url`, `instance`, and for
external adapters `args`, `setup`, `timeout_seconds`). commands, working
directories and process environments come from settings, so whoever can edit a
backend cannot run a program on the worker.

## permissions

- `add` on runs starts a plan or drift run.
- `approve` on runs approves, rejects and resumes. object permissions apply, so
  approval can be scoped, e.g. by flow.

## demo

`demo/` holds a five-minute walkthrough: a fabric inventory in git, planned into
netbox, approved by a second user, and a plan that goes stale when netbox changes
under it. `demo/setup.sh` builds it, `demo/README.md` walks through it.

## development

```sh
dev/compose up -d --build --wait     # netbox 4.6.2 on :8011, with the plugin mounted
dev/manage test netbox_alembic       # plugin tests
dev/e2e.sh                           # plan, approve, apply, stale and drift, for real
NETBOX_IMAGE=docker.io/netboxcommunity/netbox:v4.7.1 NETBOX_PORT=8012 dev/compose up -d --build --wait
```

the core's tests run the real cli against a json-file adapter:

```sh
pip install -e "alembic-runner[dev]" alembic-adapter-sdk
cd alembic-runner && python -m unittest discover -s tests
```
