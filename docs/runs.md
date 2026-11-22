# how a run goes

this page holds for both hosts. where netbox and nautobot differ, it says so.

a **backend** is something alembic plans against: another system, or the
netbox or nautobot the app runs in, reached through its own rest api like any
other. a **flow** names an inventory and a target backend. the inventory lives
in a netbox data source or a nautobot git repository. a **run** is one plan of a
flow and at most one apply of it.

a flow can also name a **source** backend and a **map spec**. with a source, the
run imports from it (`alembic import`, the inventory file selecting which types),
so the host can be the source and another system the target. a map spec
(`alembic map`, a file next to the inventory) reshapes the inventory into the
target's model on the way, as netbox to nautobot needs. the run keeps what the
import and the map produced, and plans and applies from that.

```text
plan (background job: fetch the flow's files, snapshot them, alembic plan)
  → awaiting approval
  → approve (with the approve permission, or a nautobot approval workflow)
  → apply of the approved plan bytes (alembic apply, which checks the target first)
```

the plan job fetches the files the host's way: netbox syncs the data source,
nautobot pulls the git repository. with `require_distinct_approver` (the
default), someone other than the requester decides a run.

- plan never writes to a backend. schema work shows up in the plan and happens
  at apply.
- apply writes exactly the plan that was approved: the job checks the stored
  plan against the hash recorded at approval.
- if the target changed between plan and apply, alembic refuses the plan, the
  run ends `stale`, and nothing is written. plan again.
- a failed apply can be resumed; alembic checks the target again and its
  journal skips what already landed.
- a newer plan supersedes the flow's waiting plan and takes away a pending
  resume. editing the flow supersedes every run of it that is not applying
  yet, approved ones included. a flow has one run in progress at a time, and a
  target one apply. an approved run whose target is busy stays approved and
  says why; resume starts it once the other apply is done.

a drift run (`alembic plan --report`) records how the target differs from the
inventory and never goes to approval.

## rest api

both hosts serve the same endpoints under `/api/plugins/alembic/`:

- `backends/`, `flows/`: list, create, read, update and delete, like any other
  model.
- `runs/`: list and read only. runs start through a flow.
- `POST flows/<id>/plan/`: start a plan, or a drift run with
  `{"kind": "drift"}`. needs `add` on runs.
- `POST runs/<id>/approve/`, `runs/<id>/reject/`: decide a run that awaits
  approval. needs `approve` on runs. on nautobot, a run with a pending approval
  workflow is decided there instead.
- `POST runs/<id>/resume/`: start an approved run whose target was busy, or
  resume a failed apply. needs `approve` on runs.
- `GET runs/<id>/plan/`: the plan exactly as alembic wrote it.

an action that is not allowed answers `403`; one the run's state does not allow
answers `409` with the reason.
