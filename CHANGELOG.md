# changelog

## Unreleased

- editing a flow supersedes its runs that are not applying yet, so an approval no longer carries over to a changed flow
- **breaking** runs need alembic 0.13: `alembic-runner` refuses anything outside `>= 0.13, < 0.14`
- **breaking** alembic 0.13 names netbox and nautobot types `app_label.model`: every netbox type with a multi-word model is renamed (`ipam.ip_address` is now `ipam.ipaddress`, `dcim.device_type` `dcim.devicetype`), and some nautobot types change too (see alembic's 0.13.0 changelog). rename them in stored inventories, map specs and alembic state before the first run on 0.13
- apply leaves the stale check to alembic, which since 0.13 re-diffs with the plan's own adopt mode, and a plan alembic refuses as stale (exit `65`) ends the run as stale. the runner's dry-run comparison is gone, and with it the check skipped on a resume: alembic checks a resumed apply as well, and lets through what already landed. so a run that failed before writing anything is checked again before it applies (#7)
- a failed command names what alembic's exit code means: a usage error (`64`), invalid input or a stale plan (`65`), a backend that could not be reached (`69`), or an apply that stopped partway with its journal kept (`75`)
- `apply_run` takes no inventory files or `resuming` flag, `hosting.start_apply` returns only the plan, and `Runner.check_stale`, `StaleCheck` and `Plan.same_work` are removed
- the docs say where a failed apply can resume: on the same worker with local state, on any worker with postgres state for the built-in backends
- a peeringdb backend takes no config keys, as alembic's peeringdb adapter takes none. one saved with `url` or `instance` passed validation and then failed every run with "exited 65"; it is now refused when saved

## [0.3.0] - 2026-10-26

- runs need alembic 0.12: `alembic-runner` refuses anything outside `>= 0.12, < 0.13`, and a drift run reads `plan --report`'s exit `2` as drift found, not as a failure
- apply passes `--no-stale-check`: the runner's own check against the approved plan, with the flow's flags, stays the one that decides
- upgrading from alembic 0.10: a plugin for an external adapter with no `instance:` gets a new identity, and with it fresh state, so pin `instance:` first; finish any interrupted apply before upgrading, since alembic 0.12 keeps postgres-state journals in the database and does not read the old ones

## [0.2.0] - 2026-09-29

- alembic plugins are backend kinds: every plugin in `plugins_dir` is offered in the form next to netbox and nautobot, and a run uses it as `alembic --backend <name>`. this replaces the `external` kind and the `external_adapters` setting

## [0.1.0] - 2026-09-29

- first release of `alembic-runner`, `netbox-alembic` (netbox 4.6+) and `nautobot-alembic` (nautobot 3.2+)
- plan in a background job, review the plan in the ui, approve as someone other than the requester, and apply exactly the approved plan after a stale check against the target
- a flow can import from a source backend and reshape the inventory with an `alembic map` spec, so netbox or nautobot can be the source and another system the target
- drift runs record how a target differs from the inventory without going to approval
- a failed apply resumes from alembic's journal
- nautobot runs are decided by nautobot approval workflows, with the app's own `approve` permission where no workflow applies
