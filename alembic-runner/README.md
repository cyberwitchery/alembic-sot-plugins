# alembic-runner

the host-independent core of the alembic netbox and nautobot plugins. it runs
the `alembic` cli for a flow's import, map, plan, apply and drift report, parses
what the cli writes, and owns the run state machine.

each run gets an environment built from scratch: the worker's own secrets never
reach alembic, and credentials go through environment variables, never a file.

it is installed as a dependency of
[netbox-alembic](https://pypi.org/project/netbox-alembic/) and
[nautobot-alembic](https://pypi.org/project/nautobot-alembic/). it needs python
3.10+ and an `alembic` binary in `SUPPORTED` (`>= 0.13, < 0.14`). everything
but `hosting` is stdlib-only.

## modules

the package root re-exports `runner`, `backend`, `plan`, `status` and
`workspace`; `pipeline`, `review` and `hosting` are imported by name.

- `runner`: `Runner` runs one cli command in a flow's directory (`plan`,
  `import_`, `map`, `apply`, `drift`) and returns an outcome with what alembic
  printed. `check_version` refuses an alembic outside `SUPPORTED`. `Flow` is
  what a run needs from the host.
- `backend`: `Backend` and `StateStore`, as the config file and environment
  alembic gets. `BUILTIN_KINDS` names alembic's built-in backends, `CONFIG_KEYS`
  is what a stored backend may set, `token_env` hands a token to a built-in
  adapter, `plugin_names` lists the alembic plugins in a directory.
- `plan`: `Plan`, `ApplyReport` and `DriftReport`, the documents the cli
  writes. a plan keeps its raw bytes and their sha256.
- `status`: `RunStatus` and `TRANSITIONS`, the run state machine, with
  `check_transition` and `failure_status`.
- `workspace`: `Workspace`, the directories a flow's runs work in.
- `pipeline`: what the plan and apply jobs do between the host's bookkeeping:
  `snapshot`, `plan_run`, `apply_run`.
- `review`: what a run's review page shows: ops grouped for reading, drift rows,
  and the names behind the uids they reference.
- `hosting`: the run bookkeeping both hosts share: moves, locks, approval checks
  and the job wrapper. it imports django, so only the host apps import it.
