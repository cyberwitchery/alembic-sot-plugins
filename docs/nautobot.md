# nautobot

[install](index.md#install) covers putting `nautobot-alembic` on nautobot.
the app has the same models, runs and guarantees as the netbox plugin. what
differs is where things come from:

- a flow's inventory lives in a nautobot **git repository**, pulled at plan time.
- a backend's token comes from a **secrets group** (access type http(s), secret
  type token), so it can live in any secrets provider nautobot has.
- **approval workflows** decide runs. a run that awaits approval starts the
  workflow whose definition matches it; its stages and approver groups decide,
  and an approval queues the apply. the app refuses an approval only the
  requester gave. a run no workflow definition matches is approved with the
  app's own `approve` permission, as in netbox.
- the jobs are hidden nautobot jobs on the `alembic` queue.

## jobs

nautobot registers an app's jobs disabled. the app's two jobs, plan and apply,
are hidden and start only from its own actions, so the app enables them when
nautobot reports its database ready, which `nautobot-server post_upgrade` does.
that is why install runs it. a run whose job is disabled never starts: a plan
stays `pending` and holds its flow, an approved run stays `approved`. run
`post_upgrade` again to enable the jobs; resume starts an approved run, and
editing the flow supersedes a pending one.

## settings

```python
PLUGINS = ["nautobot_alembic"]
PLUGINS_CONFIG = {
    "nautobot_alembic": {
        "alembic_path": "/usr/local/bin/alembic",
        "work_root": "/var/lib/nautobot-alembic",
        "state": {"backend": "postgres", "postgres_url": "postgres://alembic@db/alembic"},
        "plugins_dir": "/etc/alembic/plugins",
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
    },
}
```

each setting means what it does in [netbox](netbox.md#settings). nautobot's own
celery task time limits still apply to the jobs, so keep them above
`run_timeout_seconds`.

there is no credentials setting: a backend's token comes from its secrets group
when the job runs, through whichever secrets provider holds it. a plugin
backend (any kind that is an alembic plugin in `plugins_dir`) takes no secrets
group: its plugin file is its whole config. the same environment rules apply as
in netbox: alembic sees only the credentials of the backend each command talks
to.

## permissions

permissions are the same as netbox's: `add` on runs starts one, `approve` on
runs approves, rejects and resumes where no approval workflow applies. an
approval workflow's own stages and approver groups decide otherwise.
