from django.contrib import admin
from .models import UserRegister, LogMetrica, SystemControl

@admin.register(SystemControl)
class SystemControlAdmin(admin.ModelAdmin):
    """
    Controla se o sistema está autorizado a operar.
    Uma vez que o WhatsApp conecta (WORKING), ele marca como liberado.
    """
    list_display = ('chave', 'liberado')
    list_editable = ('liberado',)
    
    def has_add_permission(self, request):
        return SystemControl.objects.count() == 0

@admin.register(UserRegister)
class UserRegisterAdmin(admin.ModelAdmin):
    list_display = (
        'chat_id', 
        'username', 
        'appointment1_datetime', 
        'appointment2_datetime',
        'appointment3_datetime',
        'appointment4_datetime'
    )
    search_fields = ('chat_id', 'username')
    list_display_links = ('chat_id', 'username')

@admin.register(LogMetrica)
class LogMetricaAdmin(admin.ModelAdmin):
    list_display = (
        'criado_em', 
        'tipo_metrica', 
        'status', 
        'cliente_id', 
        'event_id',
        'detalhes',
    )

    list_filter = (
        'tipo_metrica', 
        'status', 
        'criado_em'
    )
    
    search_fields = (
        'cliente_id', 
        'event_id', 
        'detalhes'
    )

    ordering = ('-criado_em',)

    def has_add_permission(self, request):
        return False
        
    def has_change_permission(self, request, obj=None):
        return False