from django.urls import path
from .views import RouteAPIView, demo
urlpatterns = [
    path('api/route/', RouteAPIView.as_view(), name='route'),
    path('demo/', demo, name='demo'),
]
