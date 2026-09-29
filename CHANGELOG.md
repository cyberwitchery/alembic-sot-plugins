# changelog

## [0.2.0] - 2026-09-29

- alembic plugins are backend kinds: every plugin in `plugins_dir` is offered in the form next to netbox and nautobot, and a run uses it as `alembic --backend <name>`. this replaces the `external` kind and the `external_adapters` setting

## [0.1.0] - 2026-09-29

- first release of `alembic-runner`, `netbox-alembic` (netbox 4.6+) and `nautobot-alembic` (nautobot 3.2+)
- plan in a background job, review the plan in the ui, approve as someone other than the requester, and apply exactly the approved plan after a stale check against the target
- a flow can import from a source backend and reshape the inventory with an `alembic map` spec, so netbox or nautobot can be the source and another system the target
- drift runs record how a target differs from the inventory without going to approval
- a failed apply resumes from alembic's journal
- nautobot runs are decided by nautobot approval workflows, with the app's own `approve` permission where no workflow applies
