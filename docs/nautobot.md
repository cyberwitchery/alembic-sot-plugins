# nautobot

`nautobot-alembic` has the same models, runs and guarantees. what differs is
where things come from:

- a flow's inventory lives in a nautobot **git repository**, pulled at plan time.
- a backend's token comes from a **secrets group** (access type http(s), secret
  type token), so it can live in any secrets provider nautobot has.
- **approval workflows** decide runs. a run that awaits approval starts the
  workflow whose definition matches it; its stages and approver groups decide,
  and an approval queues the apply. the app refuses an approval only the
  requester gave. a run no workflow definition matches is approved with the
  app's own `approve` permission, as in netbox.
- the jobs are hidden nautobot jobs on the `alembic` queue.

## install

on every nautobot host, and on the worker that runs the jobs:

```sh
pip install nautobot-alembic
```

put the `alembic` binary on the worker ([releases](https://github.com/cyberwitchery/alembic/releases)
attach static binaries), add `nautobot_alembic` to `PLUGINS` in
`nautobot_config.py`, run `nautobot-server post_upgrade`, and start a worker for
the app's queue:

```sh
nautobot-server celery worker -Q alembic
```

## settings

```python
PLUGINS = ["nautobot_alembic"]
PLUGINS_CONFIG = {
    "nautobot_alembic": {
        "alembic_path": "/usr/local/bin/alembic",
        "work_root": "/var/lib/nautobot-alembic",
        # identity state. "local" keeps it in each flow's directory.
        "state": {"backend": "postgres", "postgres_url": "postgres://alembic@db/alembic"},
        # external adapter binaries by name. a backend names one, never a path.
        # an adapter that reads its token from a variable of its own names it.
        "external_adapters": {
            "mybackend": "/usr/local/bin/alembic-adapter-mybackend",
            "other": {
                "command": "/usr/local/bin/alembic-adapter-other",
                "token_env": "OTHER_TOKEN",
            },
        },
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
    },
}
```

there is no credentials setting: a backend's token comes from its secrets group
when the job runs, through whichever secrets provider holds it. the same
environment rules apply as in netbox: alembic sees only the credentials of the
backend each command talks to.

## permissions

permissions are the same as netbox's: `add` on runs starts one, `approve` on
runs approves, rejects and resumes where no approval workflow applies. an
approval workflow's own stages and approver groups decide otherwise.
