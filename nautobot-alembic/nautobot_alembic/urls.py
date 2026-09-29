from nautobot.apps.urls import NautobotUIViewSetRouter

from . import views

app_name = "nautobot_alembic"

router = NautobotUIViewSetRouter()
router.register("backends", views.BackendUIViewSet)
router.register("flows", views.FlowUIViewSet)
router.register("runs", views.RunUIViewSet)

urlpatterns = router.urls
