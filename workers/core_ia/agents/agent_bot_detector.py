import os
from groq import Groq
from workers.core_ia.services_agents.prompts_agents import prompt_bot_detector
import logging

logger = logging.getLogger(__name__)

TAG_BOT = '__CLASSIFY_BOT__'
TAG_HUMAN = '__CLASSIFY_HUMAN__'

class Agent_bot_detector():
    """
    Agente focado unicamente na detecção rápida de outros chatbots
    para otimização de recursos (blacklist/early-exit).
    """
    def __init__(self):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            self.prompt = prompt_bot_detector
            self.model = "openai/gpt-oss-20b"
        except Exception as e:
            raise EnvironmentError("A variável GROQ_API_KEY não está configurada.") from e
    
    def detect_bot(self, message: str) -> str:
        """
        Gera a classificação binária: __CLASSIFY_BOT__ ou __CLASSIFY_HUMAN__.
        
        :param history_str: O histórico completo da conversa como uma string.
        :return: A string de classificação (TAG_BOT ou TAG_HUMAN).
        """
        mensagens = [
            {
                "role": "system",
                "content": self.prompt,
            },
            {
                "role": "user",
                "content": message 
            }
        ]
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=mensagens,
                model=self.model,
                temperature=0.0 , 
            )
            result = chat_completion.choices[0].message.content.strip()

            if result == TAG_BOT:
                return TAG_BOT
            return TAG_HUMAN 
            
        except Exception as e:
            logger.error(f"Erro CRÍTICO no Agent_bot_detector (Groq): {e}", exc_info=True)
            return TAG_HUMAN