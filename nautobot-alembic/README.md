# nautobot-alembic

a nautobot app that plans, reviews and applies alembic runs. a flow names an
inventory in a nautobot git repository and a target backend; a run plans it in
a background job, a nautobot approval workflow (or, without one, someone with
the `approve` permission) decides, and the apply job writes it. before writing,
the plan is checked against the target again, and a target that changed since
the plan was made gets nothing written.

needs nautobot 3.2 or later and the `alembic` binary on the worker.
