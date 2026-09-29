# the image's own settings, plus the app. selected with NAUTOBOT_CONFIG.
# nautobot loads this file as `nautobot_config`, so the image's file is loaded
# under another name and its settings copied in.
import importlib.util

_spec = importlib.util.spec_from_file_location("image_config", "/opt/nautobot/nautobot_config.py")
_image = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_image)
globals().update({name: value for name, value in vars(_image).items() if name.isupper()})

PLUGINS = ["nautobot_alembic"]
PLUGINS_CONFIG = {
    "nautobot_alembic": {
        "work_root": "/opt/alembic-work",
    },
}
