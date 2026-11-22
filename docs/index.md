# alembic-sot-plugins

plugins that run [alembic](https://github.com/cyberwitchery/alembic) from inside
a source of truth: plan in the background, review the plan in the ui, approve,
apply.

- `alembic-runner`: the host-independent core. runs the `alembic` cli, parses
  what it writes, owns the run state machine.
- `netbox-alembic`: the netbox plugin, netbox 4.6 or later.
- `nautobot-alembic`: the nautobot app, nautobot 3.2 or later.

## install

on every netbox or nautobot host, and on the worker that runs the jobs:

```sh
pip install netbox-alembic     # netbox 4.6 or later
pip install nautobot-alembic   # nautobot 3.2 or later
```

then:

1. put the `alembic` binary on the worker. its
   [releases](https://github.com/cyberwitchery/alembic/releases) attach static
   binaries.
2. add the app to `PLUGINS` and configure it: `netbox_alembic` in
   `configuration.py` ([settings](netbox.md#settings)), `nautobot_alembic` in
   `nautobot_config.py` ([settings](nautobot.md#settings)).
3. on netbox, run `manage.py migrate`. on nautobot, run
   `nautobot-server post_upgrade`, which also [enables the app's
   jobs](nautobot.md#jobs).
4. start a worker for the app's queue. on netbox the default worker does not
   listen on it:

```sh
manage.py rqworker netbox_alembic.alembic   # netbox
nautobot-server celery worker -Q alembic    # nautobot
```

where a failed apply can resume depends on the state store. with `"local"`
state, alembic keeps the apply journal in the flow's working directory, so run
one worker for the queue with `work_root` on persistent disk: a resume has to
run where the interrupted apply ran. with `"postgres"` state, the built-in
backends keep the journal in that database and any worker can resume; an
external adapter or plugin keeps its own journal, wherever it writes it.

## pages

- [how a run goes](runs.md): backends, flows and runs, what each step
  guarantees, and the rest api.
- [netbox](netbox.md): settings, credentials and permissions of `netbox-alembic`.
- [nautobot](nautobot.md): what differs in `nautobot-alembic`.
- [development](development.md): the dev stacks, tests and releases.
