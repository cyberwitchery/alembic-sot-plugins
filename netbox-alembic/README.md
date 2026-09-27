# netbox-alembic

a netbox plugin that plans, reviews and applies alembic runs. a flow names an
inventory in a netbox data source and a target backend; a run plans it in the
background, someone with the `approve` permission reviews the plan, and the
apply job writes it. before writing, the plan is checked against the target
again, and a target that changed since the plan was made gets nothing written.

needs netbox 4.6 or later and the `alembic` binary on the worker.
