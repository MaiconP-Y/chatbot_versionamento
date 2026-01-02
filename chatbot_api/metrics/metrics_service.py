import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from chatbot_api.models import LogMetrica

logger = logging.getLogger("metrics-service")

# ═══════════════════════════════════════════════════════════════════════════════
# FUNÇÃO PRINCIPAL: Registrar Evento
# ═══════════════════════════════════════════════════════════════════════════════

def registrar_evento(
    cliente_id: str,
    event_id: str,
    tipo_metrica: str,
    status: str = 'success',
    detalhes: str = '',
) -> Dict[str, Any]:
    """
    Registra um evento de métrica no PostgreSQL (ÚNICA fonte de verdade).
    
    Função UNIFICADA com transação atômica para garantir integridade dos dados.
    
    :param cliente_id: ID do cliente (telefone ou UUID)
    :param event_id: ID do evento no calendário
    :param tipo_metrica: 'agendamento', 'cancelamento', 'lembrete'
    :param status: 'success', 'failed', 'pending'
    :param detalhes: Informações adicionais
    :return: Dict com status e informações do evento registrado
    """
    
    try:
        with transaction.atomic():
            log_metrica = LogMetrica.registrar_evento(
                cliente_id=cliente_id,
                event_id=event_id,
                tipo_metrica=tipo_metrica,
                status=status,
                detalhes=detalhes,
            )
        
        logger.info(
            f"✅ Evento registrado com sucesso | "
            f"Cliente: {cliente_id} | Tipo: {tipo_metrica} | Status: {status}"
        )
        
        return {
            'status': 'success',
            'log_id': str(log_metrica.id),
            'cliente_id': cliente_id,
            'event_id': event_id,
            'tipo_metrica': tipo_metrica,
            'timestamp': log_metrica.criado_em. isoformat(),
        }
    
    except Exception as e:
        logger.error(
            f"❌ Erro ao registrar evento: {e} | "
            f"Cliente: {cliente_id} | Event: {event_id}",
            exc_info=True
        )
        
        return {
            'status': 'error',
            'cliente_id': cliente_id,
            'event_id': event_id,
            'tipo_metrica': tipo_metrica,
            'error': str(e),
        }

# ═══════════════════════════════════════════════════════════════════════════════
# CONSULTAS: Histórico e Auditoria
# ═══════════════════════════════════════════════════════════════════════════════

def get_historico_cliente(
    cliente_id: str,
    limite_dias: int = 30,
    tipo_filtro: Optional[str] = None,
    status_filtro: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retorna o histórico completo de um cliente (últimos N dias).
    
    :param cliente_id: ID do cliente
    :param limite_dias: Número de dias a retroceder (padrão: 30)
    :param tipo_filtro: Filtrar por tipo ('agendamento', 'cancelamento', 'lembrete')
    :param status_filtro: Filtrar por status ('success', 'failed')
    :return: Lista de dicts com logs detalhados
    """
    
    try:
        data_limite = datetime.now(timezone.utc) - timedelta(days=limite_dias)
        
        queryset = LogMetrica.objects. filter(
            cliente_id=cliente_id,
            criado_em__gte=data_limite,
        ). order_by('-criado_em')
        
        if tipo_filtro:
            queryset = queryset.filter(tipo_metrica=tipo_filtro)
        
        if status_filtro:
            queryset = queryset.filter(status=status_filtro)
        
        return [
            {
                'id': str(log.id),
                'cliente_id': log.cliente_id,
                'event_id': log.event_id,
                'tipo_metrica': log.get_tipo_metrica_display(),
                'status': log.status,
                'detalhes': log.detalhes,
                'criado_em': log.criado_em.isoformat(),
            }
            for log in queryset
        ]
    
    except Exception as e:
        logger.error(f"❌ Erro ao consultar histórico: {e}")
        return []

def get_log_evento(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Retorna o log detalhado de um evento específico. 
    
    :param event_id: ID do evento
    :return: Dict com dados do evento ou None
    """
    
    try:
        log = LogMetrica. objects.filter(event_id=event_id).first()
        
        if not log:
            return None
        
        return {
            'id': str(log. id),
            'cliente_id': log.cliente_id,
            'event_id': log.event_id,
            'tipo_metrica': log.get_tipo_metrica_display(),
            'status': log.status,
            'detalhes': log.detalhes,
            'criado_em': log.criado_em.isoformat(),
        }
    
    except Exception as e:
        logger.error(f"❌ Erro ao consultar evento: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# CONSULTAS: Relatórios Agregados
# ═══════════════════════════════════════════════════════════════════════════════

def get_estatisticas_diarias(data_inicio: str, data_fim: str) -> Dict[str, Any]:
    """
    Retorna estatísticas agregadas entre duas datas.
    
    Ideal para relatórios mensal/semanal.
    
    :param data_inicio: Data no formato YYYY-MM-DD
    :param data_fim: Data no formato YYYY-MM-DD
    :return: Dict com agregações por tipo e status
    """
    
    try:
        data_inicio_obj = datetime.strptime(data_inicio, "%Y-%m-%d"). date()
        data_fim_obj = datetime.strptime(data_fim, "%Y-%m-%d").date()
        
        queryset = LogMetrica.objects. filter(
            criado_em__date__gte=data_inicio_obj,
            criado_em__date__lte=data_fim_obj,
        )
        
        stats = queryset.values('tipo_metrica', 'status').annotate(
            count=Count('id')
        ).order_by('tipo_metrica', 'status')
        
        por_tipo = queryset.filter(status='success').values('tipo_metrica').annotate(
            count=Count('id')
        ).order_by('tipo_metrica')
        
        return {
            'periodo': {
                'inicio': data_inicio,
                'fim': data_fim,
            },
            'total_eventos': queryset.count(),
            'total_sucesso': queryset.filter(status='success').count(),
            'total_falhas': queryset.filter(status='failed').count(),
            'por_tipo_status': list(stats),
            'por_tipo_sucesso': list(por_tipo),
        }
    
    except Exception as e:
        logger. error(f"❌ Erro ao consultar estatísticas: {e}")
        return {}

# ═══════════════════════════════════════════════════════════════════════════════
# LIMPEZA: Função para Remover Dados Antigos (Opcional)
# ═══════════════════════════════════════════════════════════════════════════════

def limpar_dados_antigos(dias_retencao: int = 90) -> Dict[str, int]:
    """
    Remove registros de métricas com mais de N dias.
    
    ⚠️ Use com cuidado!  Dados deletados NÃO podem ser recuperados. 
    
    :param dias_retencao: Número de dias a manter (padrão: 90)
    :return: Dict com número de registros deletados
    """
    
    try:
        data_limite = datetime.now(timezone.utc) - timedelta(days=dias_retencao)
        
        deleted_count, _ = LogMetrica.objects. filter(
            criado_em__lt=data_limite
        ).delete()
        
        logger.warning(f"🗑️ {deleted_count} registros antigos foram deletados")
        
        return {
            'status': 'success',
            'deletados': deleted_count,
            'data_limite': data_limite.isoformat(),
        }
    
    except Exception as e:
        logger.error(f"❌ Erro ao limpar dados: {e}")
        return {'status': 'error', 'error': str(e)}