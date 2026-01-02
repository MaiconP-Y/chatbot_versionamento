from services.redis_client import update_session_state, add_to_blacklist, delete_session_state, delete_history, delete_user_profile_cache

from workers.core_ia.agents.agent_register import Agent_register
from workers.core_ia.agents.agent_date import Agent_date
from workers.core_ia.agents.agent_router import Agent_router
from workers.core_ia.agents.agent_consul_cancel import Agent_cancel
from workers.core_ia.agents.agent_info import Agent_info
from workers.core_ia.utils.user_data_service import get_user_name_from_db
from workers.core_ia.agents.agent_bot_detector import Agent_bot_detector, TAG_BOT, TAG_HUMAN

import logging 
from services.redis_client import update_session_state

def count_user_messages_in_history_str(history_str: str) -> int:
    """Conta o número de mensagens de usuário no histórico para medir o progresso."""
    USER_PREFIX = "[User]:"
    return history_str.count(USER_PREFIX)

logger = logging.getLogger(__name__)

REROUTE_SIGNAL = "__FORCE_ROUTE_INTENT__" 
MENSAGEM_ERRO_SUPORTE = "Desculpe, ocorreu um erro técnico inesperado no nosso sistema de IA. Por favor, entre em contato diretamente com nosso suporte."

class agent_service(): 
    """
    Serviço de IA minimalista. Atua como proxy entre o Worker e o Roteador de Agentes.
    Gerencia o estado e formata o histórico para a API Groq.
    """
    def __init__(self):
        self.registration_agent = Agent_register()
        self.date_agent = Agent_date(router_agent_instance=self) 
        self.router_agent = Agent_router()
        self.agent_consul_cancel = Agent_cancel()
        self.agent_info = Agent_info()
        self.bot_detector = Agent_bot_detector()
        
    def router(self, history_str: str, chat_id: str, message: str = None, step_decode: str = None, reroute_signal: str = None) -> str:
        """
        Delega o trabalho de roteamento.
        """
        try:            
            user_name = get_user_name_from_db(chat_id)
            if reroute_signal == REROUTE_SIGNAL:
                step_decode = None
            
            response = ""
            
            if user_name:
                if step_decode: 
                    if step_decode in ['AGENT_DATE_SEARCH', 'AGENT_DATE_CONFIRM']:
                        response = self.date_agent.generate_date(step_decode, history_str, chat_id, user_name)
                    
                    elif step_decode == 'AGENT_CAN_VERIF':
                        response = self.agent_consul_cancel.generate_cancel(history_str, chat_id)
                    return response
                        
                else: 
                    response = self.router_agent.route_intent(history_str)
                    if response == 'ativar_agent_atendimento_humano':
                        # Centralizar o handover humano: blacklist + limpar estado + sinalizar reroute
                        add_to_blacklist(chat_id)
                        delete_history(chat_id)
                        delete_session_state(chat_id)
                        delete_user_profile_cache(chat_id)
                        final_message = ("Ok, solicitação detectada com sucesso. Um de nossos agentes entrará em contato com você em breve. "
                                         "A partir de agora, nosso bot não processará mais suas mensagens.")
                        from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS
                        return f"{REROUTE_COMPLETED_STATUS}|{final_message}"
                    if response == 'ativar_agent_marc':
                        update_session_state(chat_id, registration_step='AGENT_DATE_SEARCH')
                        response = self.date_agent.generate_date('AGENT_DATE_SEARCH', history_str, chat_id, user_name)
                        
                    elif response == 'ativar_agent_ver_cancel':
                        update_session_state(chat_id, registration_step='AGENT_CAN_VERIF')
                        response = self.agent_consul_cancel.generate_cancel(history_str, chat_id)
                    elif response == 'ativar_agent_info':
                        response = self.agent_info.generate_info(history_str, user_name)
                    return response
            else:
                user_message_count = count_user_messages_in_history_str(history_str)
                detection_result = self.bot_detector.detect_bot(message) 
                print(f"####################################{detection_result}")
                if detection_result == TAG_BOT:
                    add_to_blacklist(chat_id) 
                    logger.warning(f"🚨 CHATBOT DETECTADO (LLM - Corte Imediato na 1ª): {chat_id}. Abortando.")
                    return None 
                if detection_result == TAG_HUMAN:
                    if user_message_count >= 4:
                        add_to_blacklist(chat_id) 
                        logger.warning(f"🚨 CHATBLOQUEADO (FALTA DE PROGRESSO APÓS {user_message_count} MENSAGENS): {chat_id}. Blacklist.")
                        return None
                    # Se passou pelo bloqueio, chama o agente de registro
                    response = self.registration_agent.generate_register(history_str, chat_id)

            return response
            
        except Exception as e:
            logger.error(f"Erro CRÍTICO no serviço de IA para chat_id {chat_id}: {e}", exc_info=True)
            
            # 1. Tenta limpar a memória para evitar travamento eterno
            try:
                delete_history(chat_id)
                delete_session_state(chat_id)
            except:
                pass

            # 2. Inicializa o serviço WAHA (caso não esteja injetado)
            from services.waha_api import Waha
            waha_service = Waha()

            # 3. MENSAGEM DE TEXTO AMIGÁVEL
            msg_erro_cliente = (
                "😓 *Ops! Tive um problema técnico inesperado.*\n\n"
                "Para garantir que você seja atendido, estou enviando abaixo o contato "
                "direto do nosso *Suporte Técnico Especializado*.\n\n"
                "Por favor, encaminhe o erro ou chame no contato abaixo:"
            )
            waha_service.send_whatsapp_message(chat_id, msg_erro_cliente)

            # 4. ENVIA O SEU CARD DE CONTATO (A função que você já tem)
            # Isso fará aparecer o botão "Adicionar Contato" ou "Conversar" para o cliente
            waha_service.send_support_contact(chat_id)

            # 5. IMPORTANTE: Re-lança o erro para o Worker pegar e TE avisar
            raise e