from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import ChatSessionViewSet

router = DefaultRouter()
router.register(r"chat", ChatSessionViewSet, basename="chat")

urlpatterns = [
    path("", include(router.urls)),
]
