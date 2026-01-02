from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('portal-secreto-dev-m/', admin.site.urls), 
    path('control-quiro/', include(('chatbot_api.urls_gui', 'chatbot_api'), namespace='gui')),
    path('api/v1/', include('chatbot_api.urls_api')),
]