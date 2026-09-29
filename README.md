# alembic-sot-plugins

[![netbox-alembic](https://img.shields.io/pypi/v/netbox-alembic?label=netbox-alembic)](https://pypi.org/project/netbox-alembic/)
[![nautobot-alembic](https://img.shields.io/pypi/v/nautobot-alembic?label=nautobot-alembic)](https://pypi.org/project/nautobot-alembic/)
[![docs](https://readthedocs.org/projects/alembic-sot-plugins/badge/?version=latest)](https://alembic-sot-plugins.readthedocs.io/)

plugins that run [alembic](https://github.com/cyberwitchery/alembic) from inside
a source of truth: plan in the background, review the plan in the ui, approve,
apply.

- `alembic-runner`: the host-independent core. runs the `alembic` cli, parses
  what it writes, owns the run state machine.
- `netbox-alembic`: the netbox plugin, netbox 4.6 or later.
- `nautobot-alembic`: the nautobot app, nautobot 3.2 or later.

```sh
pip install netbox-alembic     # or nautobot-alembic
```

full docs at [alembic-sot-plugins.readthedocs.io](https://alembic-sot-plugins.readthedocs.io/):
how a run goes, installing and configuring each plugin, and development.
