import os
from groq import Groq
from workers.core_ia.services_agents.prompts_agents import prompt_info
from services.service_api_calendar import ServicesCalendar
import logging 

logger = logging.getLogger(__name__) 

CLINICA_NOME = os.getenv("CLINICA_NOME")
CLINICA_EMAIL = os.getenv("CLINICA_EMAIL")
CLINICA_ENDERECO = os.getenv("CLINICA_ENDERECO")

groq_service = Groq()
services_calendar = ServicesCalendar()

class Agent_info():
    """
    Classe de serviço dedicada a interagir com a API da Groq, usando o histórico completo (history_str)
    para manter o contexto e delegar ações de registro via Tool Calling.
    """
    def __init__(self):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        except Exception as e:
            raise EnvironmentError("A variável GROQ_API_KEY não está configurada.") from e
    def generate_info(self, history_str: str, user_name: str) -> str:
        """
        Gera uma resposta da IA, usando a string do histórico completo como a última mensagem do usuário.
        """
        contexto_dinamico = f"""Você é o Assistente Virtual do {CLINICA_NOME}.
# DADOS DA CLÍNICA (Contexto Verdadeiro):
- Endereço: {CLINICA_ENDERECO}
- Horário de Funcionamento: Segunda a Sexta, das 07:00 às 19:00.
- Email de Suporte: {CLINICA_EMAIL}
- Nome do Usuário: {user_name}
"""
        mensagens = [
            {
                "role": "system",
                "content": f"{contexto_dinamico}\n{prompt_info}",
            },
            {
                "role": "user",
                "content": history_str
            }
        ]
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=mensagens,
                model="openai/gpt-oss-20b",
                temperature=0.0 , 
            )

            response_message = chat_completion.choices[0].message
            resposta_ia = response_message.content
            
            return resposta_ia
            
        except Exception as e:
            logger.error(f"Erro CRÍTICO no Agent_info (Groq): {e}", exc_info=True)

            raise 