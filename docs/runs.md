# how a run goes

a **backend** is something alembic plans against: another system, or this
netbox itself, reached through its own rest api like any other. a **flow** names
an inventory in a netbox data source and a target backend. a **run** is one plan
of a flow and at most one apply of it.

a flow can also name a **source** backend and a **map spec**. with a source, the
run imports from it (`alembic import`, the inventory file selecting which types),
so netbox can be the source and another system the target. a map spec
(`alembic map`, a file in the same data source) reshapes the inventory into the
target's model on the way, as netbox to nautobot needs. the run keeps what the
import and the map produced, and plans, checks and applies from that.

```text
plan (background job: sync the data source, snapshot the files, alembic plan)
  → awaiting approval
  → approve (someone other than the requester, with the approve permission)
  → stale check (alembic plan --dry-run against the snapshot)
  → apply of the approved plan bytes
```

- plan never writes to a backend. schema work shows up in the plan and happens
  at apply.
- apply writes exactly the plan that was approved: the job checks the stored
  plan against the hash recorded at approval.
- if the target changed between plan and apply, the run ends `stale` and
  nothing is written. plan again.
- a failed apply can be resumed; alembic's journal skips what already landed.
- a newer plan supersedes the flow's waiting plan and takes away a pending
  resume. a flow has one run in progress at a time, and a target one apply.

a drift run (`alembic plan --report`) records how the target differs from the
inventory and never goes to approval.
