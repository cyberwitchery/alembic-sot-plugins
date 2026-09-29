# alembic-sot-plugins

plugins that run [alembic](https://github.com/cyberwitchery/alembic) from inside
a source of truth: plan in the background, review the plan in the ui, approve,
apply.

- `alembic-runner`: the host-independent core. runs the `alembic` cli, parses
  what it writes, owns the run state machine.
- `netbox-alembic`: the netbox plugin, netbox 4.6 or later.
- `nautobot-alembic`: the nautobot app, nautobot 3.2 or later.

## install

```sh
pip install netbox-alembic     # netbox 4.6 or later
pip install nautobot-alembic   # nautobot 3.2 or later
```

both need the `alembic` binary on the worker that runs their jobs; its
[releases](https://github.com/cyberwitchery/alembic/releases) attach static
binaries. [netbox](netbox.md) and [nautobot](nautobot.md) walk through the rest.

## pages

- [how a run goes](runs.md): backends, flows and runs, and what each step guarantees.
- [netbox](netbox.md): installing and configuring `netbox-alembic`.
- [nautobot](nautobot.md): installing and configuring `nautobot-alembic`.
- [development](development.md): the dev stacks, tests and releases.
