import logging
from typing import Dict, Any
from workers.core_api.django_api_service import DjangoApiService
from services.redis_client import push_dashboard_metric
logger = logging.getLogger("metrics-client-service")

def registrar_evento(
    cliente_id: str,
    event_id: str,
    tipo_metrica: str,
    status: str = 'success',
    detalhes: str = '',
) -> Dict[str, Any]:
    """
    [FUNÇÃO UNIFICADA NO WORKER]
    Dispara o log de métrica para o Django BaaS via chamada HTTP.
    
    Esta função substitui a antiga lógica que usava o ORM.
    """
    
    payload = {
        'cliente_id': cliente_id,
        'event_id': event_id,
        'tipo_metrica': tipo_metrica,
        'status': status,
        'detalhes': detalhes,
    }
    push_dashboard_metric(
        chat_id=cliente_id, 
        status=status, 
        detalhes=detalhes, 
        tipo_metrica=tipo_metrica
    )
    response = DjangoApiService.log_metric(payload)
    if response.get('status') == 'SUCCESS':
        logger.debug(f"Métrica registrada com sucesso: {tipo_metrica} | {status}")
    else:
        logger.warning(f"Falha ao registrar métrica (HTTP status: {response.get('status')}): {response.get('message')}")
        
    return response