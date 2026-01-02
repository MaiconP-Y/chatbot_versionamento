from django.urls import path
from . import views

urlpatterns = [
    path('', views.admin_view, name='admin_control_panel'),
    path('auth/google/', views.google_auth_redirect, name='google_auth'),
    path('admin/waha-qr/', views.waha_qr_proxy, name='waha_qr_proxy'),
    path('control/waha-status/', views.check_waha_status, name='waha_status'),
    path('control/handoff/activate/', views.assumir_conversa_api, name='activate_handoff'),
    path('control/handoff/deactivate/', views.deactivate_handoff, name='deactivate_handoff'),
    path('control/users/delete/', views.delete_users_api, name='delete_users_api'),
    path('control/system/flush/', views.emergency_redis_flush, name='emergency_flush'),
]