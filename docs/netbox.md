# netbox

## install

on every netbox host, and on the worker that runs the jobs:

```sh
pip install netbox-alembic
```

put the `alembic` binary on the worker ([releases](https://github.com/cyberwitchery/alembic/releases)
attach static binaries), add `netbox_alembic` to `PLUGINS` in
`configuration.py`, run `manage.py migrate`, and start a worker for the
plugin's queue. the default worker does not listen on it:

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
        # credentials by name: each value names worker environment variables.
        # "token" feeds the adapter's own token variable (NETBOX_TOKEN, ...);
        # "env" sets adapter variables for external adapters.
        "credentials": {
            "netbox": {"token": "ALEMBIC_NETBOX_TOKEN"},
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
