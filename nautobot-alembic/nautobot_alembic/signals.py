def enable_jobs(sender, **kwargs):
    """nautobot installs an app's jobs disabled; these run only from the app's own
    actions, so they are enabled once the database is ready."""
    from nautobot.extras.models import Job

    Job.objects.filter(module_name__startswith="nautobot_alembic.").update(enabled=True)
