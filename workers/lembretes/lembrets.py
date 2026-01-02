import logging
import re
from datetime import datetime, timedelta, timezone
import os
from services.service_api_calendar import ServicesCalendar, BR_TIMEZONE
from services.waha_api import Waha
from services.metrics import registrar_evento
from workers.lembretes.redis_lembrets import lembrete_ja_enviado

logger = logging.getLogger("celery-reminder")
TTL_TWO_HOURS = 7200

def extract_phone_and_name(payload: str) -> dict | None:
    """Extrai dados do resumo do evento usando Regex robusto."""
    if not payload:
        return None
    padrao = r"Nome:\s*(.+?)\s*-\s*Cliente ID:\s*(.+)"
    match = re.search(padrao, payload)
    if match:
        return {
            "nome": match.group(1).strip(),
            "cliente_id": match.group(2).strip()
        }
    return None

def process_reminders():
    """
    Busca eventos e envia lembretes via OAuth2.
    Implementa trava de idempotência para evitar duplicidade.
    """
    logger.info("🚀 Iniciando processamento de lembretes via OAuth2.")
    ServicesCalendar.inicializar_servico()
    service = ServicesCalendar.service
    
    if not service:
        logger.error("❌ Falha na autenticação do Google Calendar.")
        return

    try:
        now = datetime.now(timezone.utc)
        start_check = now + timedelta(hours=2)
        end_check = now + timedelta(hours=2, minutes=15)

        events_result = service.events().list(
            calendarId=os.environ.get('ID_AGENDA', 'primary'),
            timeMin=start_check.isoformat(),
            timeMax=end_check.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events:
            return

        event = events[0]
        data = extract_phone_and_name(event.get("summary", ""))
        
        if not data:
            logger.warning(f"⚠️ Evento {event.get('id')} com formato inválido.")
            return

        cliente_id = data["cliente_id"]
        hoje = datetime.now(BR_TIMEZONE).strftime("%Y-%m-%d")
        trava_chave = f"lembrete:enviado:{cliente_id}:{hoje}"

        if lembrete_ja_enviado(trava_chave, TTL_TWO_HOURS):
            logger.info(f"⏭️ Lembrete de hoje já foi enviado para {data['nome']}. Finalizando.")
            return
        
        start_iso = event.get("start", {}).get("dateTime")
        dt_local = datetime.fromisoformat(start_iso.replace('Z', '+00:00')).astimezone(BR_TIMEZONE)
        msg = f"Olá {data['nome']}, passando para lembrar da sua consulta hoje às *{dt_local.strftime('%H:%M')}*."
        Waha().send_whatsapp_message(cliente_id, msg)
        registrar_evento(cliente_id, event.get("id"), 'lembrete', 'success', "Único enviado")
        logger.info(f"✅ Sucesso: Lembrete único enviado para {data['nome']}.")

    except Exception as e:
        logger.error(f"❌ Erro: {e}", exc_info=True)