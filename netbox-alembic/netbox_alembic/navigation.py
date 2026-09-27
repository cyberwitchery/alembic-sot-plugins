from netbox.plugins import PluginMenuButton, PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_alembic:flow_list",
        link_text="flows",
        permissions=["netbox_alembic.view_flow"],
        buttons=(
            PluginMenuButton(
                "plugins:netbox_alembic:flow_add",
                "add",
                "mdi mdi-plus-thick",
                permissions=["netbox_alembic.add_flow"],
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_alembic:run_list",
        link_text="runs",
        permissions=["netbox_alembic.view_run"],
    ),
    PluginMenuItem(
        link="plugins:netbox_alembic:backend_list",
        link_text="backends",
        permissions=["netbox_alembic.view_backend"],
        buttons=(
            PluginMenuButton(
                "plugins:netbox_alembic:backend_add",
                "add",
                "mdi mdi-plus-thick",
                permissions=["netbox_alembic.add_backend"],
            ),
        ),
    ),
)
