from celery import shared_task
import logging
import os
from django.conf import settings
from chatbot_api.models import SystemControl  # Nova tabela de trava
from workers.lembretes.lembrets import process_reminders
from workers.cleanup.cleanup_service import run_daily_cleanup

logger = logging.getLogger(__name__)

@shared_task(name="send_scheduled_reminders")
def send_scheduled_reminders_task():
    """
    Task de envio de lembretes automáticos.
    Verifica a trava de segurança e a existência das credenciais.
    """

    trava = SystemControl.objects.filter(chave="waha_pode_iniciar").first()
    if not trava or not trava.liberado:
        logger.warning("🚨 Task abortada: Sistema travado ou aguardando primeira conexão WhatsApp.")
        return

    path_json = os.path.join(settings.BASE_DIR, 'credentials_service.json')
    if not os.path.exists(path_json):
        logger.error("🚨 Task abortada: Arquivo 'credentials_service.json' não encontrado!")
        return

    try:
        logger.info("📅 Iniciando processamento de lembretes...")
        process_reminders()
        logger.info("✅ Processamento de lembretes concluído.")
    except Exception as e:
        logger.error(f"❌ Erro ao processar lembretes: {e}")

@shared_task(name="cleanup_expired_appointments_task")
def cleanup_expired_appointments_task():
    """
    [Celery Task] Aciona a lógica de negócio de limpeza de DB.
    """
    try:
        run_daily_cleanup()
        logger.info("🧹 Task de limpeza finalizada pelo Celery.")
    except Exception as e:
        logger.error(f"❌ Erro na task de limpeza: {e}")