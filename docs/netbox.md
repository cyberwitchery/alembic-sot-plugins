# netbox

[install](index.md#install) covers putting `netbox-alembic` on netbox. this page
covers its settings, backends, credentials and permissions.

## settings

```python
PLUGINS = ["netbox_alembic"]
PLUGINS_CONFIG = {
    "netbox_alembic": {
        "alembic_path": "/usr/local/bin/alembic",
        "work_root": "/var/lib/netbox-alembic",
        "state": {
            "backend": "postgres",
            "postgres_url": "postgres://alembic@db/alembic",
            "postgres_tls": "require",
        },
        # credentials by name: each value names worker environment variables.
        # "token" feeds a built-in adapter's token variable (NETBOX_TOKEN, ...);
        # "env" sets the variables a plugin's adapter reads.
        "credentials": {
            "netbox": {"token": "ALEMBIC_NETBOX_TOKEN"},
            "lab-nautobot": {"token": "LAB_NAUTOBOT_TOKEN"},
        },
        "plugins_dir": "/etc/alembic/plugins",
        "require_distinct_approver": True,
        "plan_ttl_hours": 72,
        "run_timeout_seconds": 1800,
        "rust_log": "info",
    },
}
```

- `alembic_path`: the alembic binary. default `alembic`, from the worker's
  `PATH`.
- `work_root`: where each flow gets its working directory. default
  `/opt/alembic-work`.
- `state`: where alembic keeps identity state. `"local"` (the default) keeps it
  in each flow's directory. `"postgres"` keeps it in the database at
  `postgres_url`, and `postgres_tls` is alembic's `disable` (its default) or
  `require`; alembic refuses any other value.
- `credentials`: see [backends and credentials](#backends-and-credentials).
- `plugins_dir`: alembic's plugins directory. each plugin in it is a backend
  kind. unset, there are none.
- `require_distinct_approver`: someone other than the requester approves or
  rejects a run. default `True`.
- `plan_ttl_hours`: how long a plan can wait for approval, counted from when it
  was made. approving an older one ends the run `expired`. `0` or `None` lets a
  plan wait forever. default `72`.
- `run_timeout_seconds`: how long one alembic command may run before it is
  killed. the run then fails; an apply that timed out can be resumed. default
  `1800`. netbox's own job timeout (`RQ_DEFAULT_TIMEOUT`) still applies, so keep
  it above this.
- `rust_log`: alembic's `RUST_LOG` filter, such as `"info"` or
  `"info,alembic_engine=debug"`. its log lines land in the run's output. unset,
  alembic logs warnings only.

## backends and credentials

secrets never enter the database. a backend stores the name of a credential,
and the worker reads the value from its environment at run time. alembic runs
with an environment built for the run, so the worker's own secrets (django
`SECRET_KEY`, database passwords) do not reach it, and each command sees only
the credentials of the backend it talks to.

a backend's kind is a built-in one (netbox, nautobot, infrahub, peeringdb) or an
[alembic plugin](https://github.com/cyberwitchery/alembic/blob/main/docs/cli.md#plugins):
each `.yaml` or `.yml` file in `plugins_dir` is one, named for the file, and a
run uses it as `alembic --backend <name>`. a built-in backend's config holds only
connection keys: `url` and `instance` for netbox, nautobot and infrahub, none
for peeringdb. a plugin backend has none, its plugin file is its config.
commands, working directories and process environments come from files on the
host, so whoever can edit a backend cannot run a program on the worker.

## permissions

- `add` on runs starts a plan or drift run.
- `approve` on runs approves, rejects and resumes. object permissions apply, so
  approval can be scoped, e.g. by flow.
