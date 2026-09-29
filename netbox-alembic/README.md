# netbox-alembic

a netbox plugin that plans, reviews and applies alembic runs. a flow names an
inventory in a netbox data source and a target backend; a run plans it in the
background, someone with the `approve` permission reviews the plan, and the
apply job writes it. before writing, the plan is checked against the target
again, and a target that changed since the plan was made gets nothing written.

needs netbox 4.6 or later and the `alembic` binary on the worker.

## install

on every netbox host, and on the worker that runs the jobs:

```sh
pip install netbox-alembic
```

put the `alembic` binary on the worker (its [releases](https://github.com/cyberwitchery/alembic/releases)
attach static binaries), add `netbox_alembic` to `PLUGINS` in
`configuration.py`, run `manage.py migrate`, and start a worker for the
plugin's queue, which the default worker does not listen on:

```sh
manage.py rqworker netbox_alembic.alembic
```

settings, credentials and permissions are documented in the
[docs](https://alembic-sot-plugins.readthedocs.io/).
