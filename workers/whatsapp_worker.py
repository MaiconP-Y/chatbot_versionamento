import json, logging, os, time, traceback

from services.redis_client import (
    add_message_to_history, 
    get_recent_history,
    get_redis_client,
    get_session_state, 
    delete_history
)
from services.waha_api import Waha
from workers.core_ia.ia_core import agent_service
from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("whatsapp-worker")
QUEUE_NAME = "new_user_queue"

class WhatsAppWorker:
    def __init__(self): 
        self.redis_client = None
        self.setup_connections()
        self.redis_client = get_redis_client()
        self.service_agent = agent_service()
        self.service_waha = Waha()
    def connect(self):
        """Garante conexão com Redis e IA"""
        while True:
            try:
                self.redis = get_redis_client()
                self.redis.ping()
                if not self.agent: self.agent = agent_service()
                logger.info("✅ Worker Conectado!")
                break
            except Exception as e:
                logger.error(f"❌ Falha de Conexão: {e}. Re-tentando em 5s...")
                time.sleep(5)
    def setup_connections(self):
        try:
            self.redis_client = get_redis_client()
            self.redis_client.ping()
        except Exception as e:
            logger.error(f"❌ Erro na configuração do Worker: {e}")
            raise

    def process_incoming_message_data(self, raw_json_payload):
        """
        Lógica: Decodificar -> Duplicata Check -> Processar -> Re-enfileirar (se falhar).
        """
        try:
            main_data = json.loads(raw_json_payload.decode('utf-8'))
        except Exception as e:
            logger.error(f"❌ Erro ao decodificar JSON: {e}")
            return 

        try:
            message_data = main_data.get("payload", {})
            chat_id = message_data.get("from")
            message_text = message_data.get("body", "").strip()
            message_type = message_data.get("_data", {}).get("type")
            if message_type != 'chat':
                friendly_message = "Olá! Por favor, *envie sua mensagem como texto digitado* para que eu possa processá-la. Não consigo processar áudios, imagens, vídeos ou outros formatos no momento. Obrigado pela compreensão!"
                self.service_waha.send_whatsapp_message(chat_id, friendly_message)
                logger.info(f"Tipo de mensagem '{message_type}' detectado e rejeitado para {chat_id}. Worker finalizado.")
                return
            
            session_data = get_session_state(chat_id)
            step_bytes = session_data.get(b'registration_step') 
            active_step_decode = step_bytes.decode('utf-8') if step_bytes else None

            add_message_to_history(chat_id, "User", message_text)

            history = get_recent_history(chat_id, limit=10)
            history_str = "\n".join(history)
            
            self.service_waha.start_typing(chat_id)
            try:
                response = self.service_agent.router(history_str, chat_id, step_decode=active_step_decode, message = message_text) 
            finally:
                self.service_waha.stop_typing(chat_id)

            if response is None:
                delete_history(chat_id) 
                logger.info(f"🚫 Chat ID {chat_id} foi recém-blacklistado pela IA. WORKER IGNORANDO RESPOSTA.")
                return

            if response.strip().startswith(REROUTE_COMPLETED_STATUS):
                _, final_bot_response = response.split('|', 1) 
                self.service_waha.send_whatsapp_message(chat_id, final_bot_response)   

                logger.info(f"Processamento de RE-ROTEAMENTO BEM-SUCEDIDO para {chat_id}. Worker finalizado.")
                return
            
            self.service_waha.send_whatsapp_message(chat_id, response)
            add_message_to_history(chat_id, "Bot", response)
            logger.info(f"Processamento para {chat_id} BEM-SUCEDIDO. Histórico Bot SALVO.")
            
        except Exception as e:
            logger.error(f"❌ Falha CRÍTICA no processamento do Worker para {chat_id}: {e}")
            suporte_id = os.environ.get("SUPORTE_WA_ID")
            admin_target = f"{suporte_id}@c.us"

            try:
                tb_info = traceback.format_exc()
                stack_resumido = tb_info[-400:] 
                
                msg_admin = (
                    f"🚨 *ALERTA DE FALHA CRÍTICA*\n"
                    f"--------------------------------\n"
                    f"👤 *Cliente Afetado:* `{chat_id}`\n"
                    f"🔥 *Erro:* {str(e)}\n"
                    f"⏰ *Hora:* {time.strftime('%H:%M:%S')}\n"
                    f"--------------------------------\n"
                    f"🛠️ *Stacktrace (Final):*\n`{stack_resumido}`"
                )
                
                logger.info(f"Enviando alerta para Admin: {admin_target}")
                self.service_waha.send_whatsapp_message(admin_target, msg_admin)
                
            except Exception as alert_error:
                logger.error(f"FALHA DUPLA: Não consegui avisar o admin. Erro: {alert_error}") 

    def listen_queue(self):
        queue_name = QUEUE_NAME
        logger.info(f"Worker INICIADO. Aguardando mensagens na fila persistente '{queue_name}' (BLPOP)...")

        while True:
            try:
                result = self.redis_client.blpop(queue_name, timeout=30) 
                if result:
                    raw_json_payload = result[1] 
                    logger.info(f"📨 Payload LIDO da fila persistente.")
                    self.process_incoming_message_data(raw_json_payload)

            except Exception as e:
                logger.error(f"❌ Erro no loop de escuta (worker): {e}")
                import time; time.sleep(5)
                
    def run(self):
        logger.info("🚀 WhatsApp Worker INICIADO - Versão Corrigida")
        try:
            self.listen_queue()
        except KeyboardInterrupt:
            logger.info("⏹️ Worker interrompido pelo usuário")
        except Exception as e:
            self.connect()
            logger.error(f"💥 Erro fatal no worker: {e}")
            raise

if __name__ == "__main__":
    worker = WhatsAppWorker()
    worker.run()