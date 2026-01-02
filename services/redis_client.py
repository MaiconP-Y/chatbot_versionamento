import redis
import logging
import os
import json
from datetime import datetime

logger = logging.getLogger(__name__)

_redis_client = None 

REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB', 0))

USER_PROFILE_CACHE_TTL = 60 * 180
USER_PROFILE_CACHE_PREFIX = "cache:user_profile:"

BLACKLIST_KEY = "system:blacklist:chat_ids"
BLACKLIST_TTL_DAYS = 365

REDIS_METRICS_DB = 5
METRICS_LOG_KEY = "dashboard:logs:"

def delete_user_profile_cache(chat_id: str):
    """
    Deleta o cache de perfil do usuário, forçando o sistema a recarregar
    os dados (incluindo agendamentos) do BaaS na próxima consulta.
    """
    r = get_redis_client() 
    if r is None:
        logger.warning("⚠️ Redis indisponível. Falha ao deletar cache de perfil.")
        return
    
    key = f"cache:user_profile:{chat_id}"
    result = r.delete(key) 
    if result > 0:
        logger.info(f"🗑️ Cache de perfil DELETADO com sucesso para {chat_id}.")
    else:
        logger.info(f"ℹ️ Tentativa de deleção do cache para {chat_id}, mas a chave não existia.")

def get_user_profile_cache(chat_id: str) -> dict | None:
    """Busca o perfil de usuário do cache Redis."""
    key = USER_PROFILE_CACHE_PREFIX + chat_id
    
    try:
        cached_data = get_redis_client().get(key)
        if cached_data:
            return json.loads(cached_data) 
    except Exception as e:
        logger.error(f"❌ Falha ao buscar cache para {chat_id}: {e}")
        return None 
    
    return None

def set_user_profile_cache(chat_id: str, data: dict):
    """Salva o perfil de usuário no cache Redis com um TTL."""
    key = USER_PROFILE_CACHE_PREFIX + chat_id
    
    try:
        serialized_data = json.dumps(data) 
        get_redis_client().set(key, serialized_data, ex=USER_PROFILE_CACHE_TTL)
    except Exception as e:
        logger.warning(f"⚠️ Falha ao salvar cache para {chat_id}: {e}")

def get_redis_client():
    """
    Inicializa e retorna o cliente Redis de forma lazy (sob demanda) e segura.
    Implementa o padrão Singleton: cria a conexão apenas uma vez por processo.
    """
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client

    try:
        _redis_client = redis.Redis(
            host=REDIS_HOST, 
            port=REDIS_PORT, 
            db=REDIS_DB,
            decode_responses=False, 
            socket_connect_timeout=5, 
            socket_timeout=None,
        )
        _redis_client.ping()
        logger.info("Conexão com Redis estabelecida com sucesso via get_redis_client!")
        return _redis_client
        
    except Exception as e:
        _redis_client = None
        logger.error(f"Erro CRÍTICO ao conectar ao Redis: {e}", exc_info=True)
        raise ConnectionError(f"Falha na inicialização do cliente Redis: {e}") 

# --- Funções de Histórico (Todas devem usar get_redis_client()) ---

def get_history_key(chat_id: str) -> str:
    return f"history:{chat_id}"

TTL_TWO_HOURS = 7200

def add_message_to_history(chat_id: str, sender: str, message: str) -> int:
    """
    Adiciona uma mensagem ao histórico do usuário (Bot ou User) 
    e renova o TTL para 2 horas (7200s).
    """
    r = get_redis_client()
    history_key = get_history_key(chat_id)
    message_entry = f"[{sender}]: {message}"
    new_size = r.lpush(history_key, message_entry)
    r.expire(history_key, TTL_TWO_HOURS)
    logger.info(f"⏰ TTL do histórico de {chat_id} renovado para 2 horas.")
    return new_size

def get_recent_history(chat_id: str, limit: int = 10) -> list:
    """Retorna as N mensagens mais recentes do histórico."""
    r = get_redis_client()
    history = r.lrange(get_history_key(chat_id), 0, limit - 1)
    decoded_history = [item.decode('utf-8') for item in history]
    return decoded_history[::-1]

def get_full_history(chat_id: str) -> list:
    """Retorna todo o histórico de mensagens (mais recente primeiro)"""
    r = get_redis_client()
    history = r.lrange(get_history_key(chat_id), 0, -1)
    return history[::-1]

# --- Funções de Estado de Sessão (Todas devem usar get_redis_client()) ---

def get_session_key(chat_id: str) -> str:
    return f"session:{chat_id}"

def get_session_state(chat_id: str) -> dict:
    """Recupera os dados de estado da sessão do usuário."""
    r = get_redis_client()
    state = r.hgetall(get_session_key(chat_id))
    return state

def update_session_state(chat_id: str, **kwargs):
    """Atualiza estado da sessão"""
    r = get_redis_client()
    session_key = f"session:{chat_id}"
    
    for field, value in kwargs.items():
        r.hset(session_key, field, str(value))
    r.expire(session_key, 7200)
    logger.info(f"Estado atualizado: {chat_id} -> {kwargs}")

def check_and_set_message_id(message_id: str) -> bool:
    """
    Verifica se o ID da mensagem já foi processado.
    Se não, armazena o ID e retorna True. O ID expira em 60 segundos (TTL).

    :param message_id: O ID único da mensagem.
    :return: True se a mensagem é NOVA, False se for DUPLICADA.
    """
    r = get_redis_client()
    key = f"processed_msg:{message_id}"
    is_new = r.set(key, 1, ex=60, nx=True)
    return is_new is not None

#FINALIZAÇÃO:
def delete_session_state(chat_id: str):
    """Remove o estado de sessão temporário do usuário."""
    r = get_redis_client()
    r.delete(get_session_key(chat_id))
    logger.info(f"🗑️ Estado de sessão DELETADO para {chat_id}.")

def delete_history(chat_id: str):
    """Remove todo o histórico de conversas do usuário."""
    r = get_redis_client()
    r.delete(get_history_key(chat_id))
    logger.info(f"🗑️ Histórico de conversas DELETADO para {chat_id}.")

#BLACKLIST
def add_to_blacklist(chat_id: str) -> bool:
    """Adiciona um chat_id à lista negra (SET) e retorna True se for uma nova adição."""
    r = get_redis_client()
    if r is None:
        logger.warning(f"⚠️ Redis indisponível. Falha ao adicionar {chat_id} à blacklist.")
        return False
    is_newly_added = r.sadd(BLACKLIST_KEY, chat_id)
    logger.info(f"⚫ Chat ID {chat_id} adicionado à lista negra.")
    return bool(is_newly_added)

def is_blacklisted(chat_id: str) -> bool:
    """Verifica se um chat_id está na lista negra (cheque O(1))."""
    r = get_redis_client()
    if r is None:
        logger.error("❌ Redis indisponível. Não foi possível checar a blacklist.")
        return False
        
    return r.sismember(BLACKLIST_KEY, chat_id)

def remove_from_blacklist(chat_id: str) -> bool:
    """Remove um chat_id do set de blacklist (libera o Bot)."""
    r = get_redis_client()
    if r is None:
        logger.warning(f"⚠️ Redis indisponível. Falha ao remover {chat_id} da blacklist.")
        return False
    is_removed = r.srem(BLACKLIST_KEY, chat_id)
    logger.info(f"🟢 Chat ID {chat_id} removido da lista negra (Bot liberado).")
    return bool(is_removed)

def get_all_blacklisted_chat_ids() -> set:
    """
    Retorna todos os chat_ids atualmente na Redis Blacklist (SMEMBERS). 
    """
    r = get_redis_client()
    if r is None:
        logger.error("❌ Redis indisponível. Não foi possível ler a blacklist.")
        return set()
    try:
        raw_members = r.smembers(BLACKLIST_KEY)
        return {member.decode('utf-8') for member in raw_members}
    except Exception as e:
        logger.error(f"❌ Erro ao buscar membros da blacklist no Redis: {e}")
        return set()
    
# --- MÉTRICAS (DB 5) ---

def get_metrics_client():
    """Retorna um cliente Redis conectado especificamente ao DB 5."""
    try:
        return redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_METRICS_DB,
            decode_responses=True
        )
    except Exception as e:
        logger.error(f"❌ Erro ao conectar no Redis DB 5: {e}")
        return None


def push_dashboard_metric(chat_id: str, status: str, detalhes: str, tipo_metrica: str):
    r = get_metrics_client()
    if not r: return

    hoje_dt = datetime.now()
    hoje_str = hoje_dt.strftime("%Y-%m-%d")
    log_key = f"{METRICS_LOG_KEY}{hoje_str}"

    amanha = hoje_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    expire_at = int(amanha.timestamp())
    
    display_phone = chat_id.split('@')[0] if chat_id else "Sistema"

    payload = json.dumps({
        "time": hoje_dt.strftime("%H:%M:%S"),
        "phone": display_phone,
        "type": tipo_metrica,
        "status": status,
        "detalhes": detalhes
    })

    try:
        pipe = r.pipeline()
        pipe.lpush(log_key, payload)
        pipe.ltrim(log_key, 0, 999)
        pipe.expireat(log_key, expire_at)
        pipe.execute()
    except Exception as e:
        logger.warning(f"⚠️ Erro métricas Redis: {e}")


def get_dashboard_logs():
    """Busca apenas a lista de eventos recentes."""
    r = get_metrics_client() 
    if not r: return {'logs': []}
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    try:
        raw_logs = r.lrange(f"{METRICS_LOG_KEY}{hoje}", 0, 49)
        return {'logs': [json.loads(l) for l in raw_logs]}
    except Exception as e:
        logger.error(f"❌ Erro ao ler logs: {e}")
        return {'logs': []}
