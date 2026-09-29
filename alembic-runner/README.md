# alembic-runner

the host-independent core of the alembic netbox and nautobot plugins. it runs
the `alembic` cli for a flow's plan, stale check, apply and drift report, parses
what the cli writes, and owns the run state machine. no django in here.

each run gets an environment built from scratch: the worker's own secrets never
reach alembic, and credentials go through environment variables, never a file.

it is installed as a dependency of
[netbox-alembic](https://pypi.org/project/netbox-alembic/) and
[nautobot-alembic](https://pypi.org/project/nautobot-alembic/); on its own it
needs only python 3.10+ and the `alembic` binary.
