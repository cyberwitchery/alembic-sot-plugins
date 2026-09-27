# demo

a five-minute walkthrough of netbox-alembic: a fabric inventory in git, planned
into netbox, reviewed and approved by someone else, and a plan that goes stale
because netbox changed under it.

## setup

```sh
demo/setup.sh        # fresh netbox 4.6.2 on :8020, git repo, users, one flow
demo/rehearse.py     # optional: walks the whole demo through the api and checks it
demo/setup.sh        # again after a rehearsal, to start from a clean netbox
```

setup takes a few minutes the first time (netbox runs its migrations). it builds
a git repo in `demo/.repo` from `demo/fabric/`, and a netbox git data source
clones it from there.

two users, both password `demo` (local demo only): **engineer**, who plans, and
**reviewer**, who approves. use two browser profiles, or a private window for
the reviewer.

the inventory (`demo/fabric/`) is two sites, fra1 and ber1, each with a spine,
two leaves and two uplinks per device, plus the catalog they need: 26 objects in
four files. ber1-leaf02 starts out `planned`.

## walkthrough

**1. plan.** as engineer, open plugins → alembic → Flows → fabric and click
**Plan**. the run lands in *Awaiting approval*. the page shows the counts, every
operation and alembic's own output:

```text
$ alembic plan
plan: 26 to create, 0 to update, 0 to delete
```

**2. approve.** the engineer sees *Someone other than the requester approves
this run.* as reviewer, open the same run and click **Approve and apply**. the
run goes *Applied* and the sites, devices, interfaces and prefixes exist in
netbox. the changelog names the `alembic` service user.

```text
$ alembic plan --dry-run  (stale check)

$ alembic apply
applied 26 operations
```

**3. a change in git.** leaf02 goes live:

```sh
demo/commit-change.sh
```

as engineer, plan again. one update, with the field change shown:

```text
plan: 0 to create, 1 to update, 0 to delete

update:
  dcim.device {"name":"ber1-leaf02"}
    status: "planned" -> "active"
```

**4. the stale plan.** before the reviewer gets to it, someone edits netbox by
hand: open device ber1-leaf02 and set its status to *offline*. now the reviewer
approves the waiting plan. the run ends *Stale*, and its page says *Nothing was
written*. the device is still *offline*.

this is the point of the demo. a plan approved on tuesday does not overwrite
what changed on wednesday: before it writes anything, the apply job plans
again and compares.

a hand edit to a field the inventory does not declare (a serial number, a
comment) would not make the plan stale. alembic only manages the fields the
inventory declares and leaves the rest alone.

**5. drift.** as engineer, click **Drift report**:

```text
drift report: 1 changed, 0 missing, 0 extra

changed (present but diverged):
  dcim.device {"name":"ber1-leaf02"}
    status: "offline" -> "active"
```

**6. converge.** the drift run offers **Plan**. plan as engineer, approve as
reviewer, applied. a second drift report says there is no drift left.

## what else to point at

- **runs** lists every run with who asked, who decided and how it ended.
- a run's **plan.json** link downloads the plan exactly as alembic wrote it,
  the bytes the approval covers.
- the flow page's run table is the history of one inventory.
