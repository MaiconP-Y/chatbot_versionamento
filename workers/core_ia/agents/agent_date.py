import os
import json 
from groq import Groq

from services.metrics import registrar_evento
from services.service_api_calendar import ServicesCalendar, validar_data_nao_passada, validar_dia_nao_domingo, validar_nao_feriado, get_temporal_context
from services.redis_client import delete_history, delete_session_state, update_session_state

from workers.core_ia.services_agents.tool_reset import finalizar_user, REROUTE_COMPLETED_STATUS, RESET_SIGNAL
from workers.core_ia.services_agents.prompts_agents import prompt_date_search, prompt_date_confirm
from workers.core_ia.services_agents.consulta_services_ia import ConsultaService 
from workers.core_ia.services_agents.tools_schemas import TOOLS_DATE_SEARCH, TOOLS_DATE_CONFIRM 
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

groq_service = Groq()

AGENT_DATE_SEARCH = "AGENT_DATE_SEARCH"
AGENT_DATE_CONFIRM = "AGENT_DATE_CONFIRM"

class Agent_date():
    """
    Classe de serviço dedicada a interagir com a API da Groq.
    """
    def __init__(self, router_agent_instance):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            ServicesCalendar.inicializar_servico()
            self.router_agent = router_agent_instance
        except Exception as e:
            raise EnvironmentError("A variável GROQ_API_KEY não está configurada.") from e
    
    def generate_date(self, step_decode: str, history_str: str, chat_id: str, user_name: str) -> str:
        calendario_ia = get_temporal_context()
        
        if step_decode == AGENT_DATE_SEARCH:
            prompt_content = prompt_date_search
            tool_schema = TOOLS_DATE_SEARCH
        elif step_decode == AGENT_DATE_CONFIRM:
            prompt_content = prompt_date_confirm
            tool_schema = TOOLS_DATE_CONFIRM
        else:
            prompt_content = prompt_date_search
            tool_schema = TOOLS_DATE_SEARCH

        mensagens = [
            {
                "role": "system",
                "content": f"""Nome do usuário: {user_name}
CALENDÁRIO DE REFERÊNCIA PARA HOJE/AMANHÃ:
{calendario_ia}
# AGENTE DE BUSCA DE HORÁRIOS
{prompt_content}"""
            },
            {
                "role": "user",
                "content": history_str
            }
        ]
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=mensagens,
                model="openai/gpt-oss-120b",
                tools=tool_schema,
                tool_choice="auto",
                temperature=0.0, 
            )

            response_message = chat_completion.choices[0].message
            resposta_ia = response_message.content
            
            if response_message.tool_calls:
                available_functions = {
                    "agendar_consulta_1h": ServicesCalendar.criar_evento,
                    "ver_horarios_disponiveis": ServicesCalendar.buscar_horarios_disponiveis,
                    "finalizar_user": finalizar_user, 
                    "exibir_proximos_horarios_flex": ServicesCalendar.exibir_proximos_horarios_flex, 
                }
                
                mensagens.append(response_message)
                
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    function_to_call = available_functions[function_name]
                    function_args = json.loads(tool_call.function.arguments)
                    
                    if function_name == "finalizar_user":
                        function_args['history_str'] = history_str    
                        result_output = finalizar_user(history_str)
                        if result_output.startswith(RESET_SIGNAL):
                            delete_session_state(chat_id) 
                            delete_history(chat_id)
                            _, message_to_reroute = result_output.split('|', 1)
                            from core_ia.ia_core import agent_service
                            service_agent_instance = agent_service()
                            clean_context_for_router = f"[User]: {message_to_reroute}"

                            response = service_agent_instance.router(
                                clean_context_for_router, 
                                chat_id, 
                                reroute_signal="__FORCE_ROUTE_INTENT__" 
                            )
                            return response 
                        
                    elif function_name == "agendar_consulta_1h":
                        start_time_str = function_args.get("start_time_str")
                        try:
                            start_dt = datetime.fromisoformat(start_time_str)
                            data_para_validacao = start_dt.strftime("%d/%m/%Y")
                        except ValueError:
                            # Se a IA alucinar no formato, forçamos erro para não quebrar
                            return "Data em formato inválido. Tente novamente."

                        # --- Validações de Negócio ---
                        validacao_passada = validar_data_nao_passada(data_para_validacao)
                        if not validacao_passada.get("status") == "SUCCESS":
                            return "A data que você informou já passou. Por favor, escolha uma data futura."

                        validacao_domingo = validar_dia_nao_domingo(data_para_validacao)
                        if not validacao_domingo.get("status") == "SUCCESS":
                            return "Não agendamos consultas aos domingos. Por favor, escolha outro dia."
                        
                        check_feriado = validar_nao_feriado(data_para_validacao)
                        if check_feriado.get('status') == 'FAILURE':
                            return check_feriado.get('message')
                        
                        function_args['calendar_id_arg'] = ServicesCalendar.calendar_id
                        function_args['chat_id'] = chat_id
                        function_args['name'] = user_name

                        resultado_tool = function_to_call(
                            service=ServicesCalendar.get_service(), 
                            **function_args
                        )
                        
                        if isinstance(resultado_tool, dict) and resultado_tool.get("status") == "SUCCESS":
                            gcal_event_id = resultado_tool.get("event_id")
                            start_time_iso = resultado_tool.get("start_time")
                            
                            baas_result = ConsultaService.criar_agendamento_db(
                                chat_id=chat_id,
                                google_event_id=gcal_event_id,
                                start_time_iso=start_time_iso 
                            )
                            
                            if baas_result.get("status") != "SUCCESS":
                                error_message = baas_result.get('message', 'Erro desconhecido ao salvar no BaaS.')
                                ServicesCalendar.deletar_evento(
                                    service=ServicesCalendar.get_service(), 
                                    calendar_id_arg=ServicesCalendar.calendar_id,
                                    event_id=gcal_event_id
                                )

                                registrar_evento(
                                    cliente_id=chat_id,
                                    event_id=gcal_event_id,
                                    tipo_metrica='agendamento',
                                    status='failed',
                                    detalhes=f"BaaS falhou. Rollback GCal executado. Erro: {error_message}"
                                )
                                delete_session_state(chat_id)
                                delete_history(chat_id)

                                return f"{REROUTE_COMPLETED_STATUS}|{error_message}"

                            dt_obj = datetime.fromisoformat(start_time_iso)
                            data_formatada = dt_obj.strftime("%d/%m/%Y")
                            hora_formatada = dt_obj.strftime("%H:%M")
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=gcal_event_id,
                                tipo_metrica='agendamento',
                                status='success',
                                detalhes=f"Agendado: {data_formatada} às {hora_formatada}"
                            )
                            delete_session_state(chat_id)
                            delete_history(chat_id)
                    
                            return (f"""{REROUTE_COMPLETED_STATUS}|Agendamento Confirmado, {user_name}. Sua consulta foi marcada para o dia *{data_formatada}* às {hora_formatada}. 
Fique tranquilo(a), enviaremos um lembrete 2 horas antes no dia agendado."""
                                )
                            
                        else:
                            gcal_error_message = resultado_tool.get('message', 'Erro desconhecido ao agendar.')
                            
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=f"falha_slot_{start_time_str}",
                                tipo_metrica='agendamento',
                                status='failed',
                                detalhes=f"Erro GCal: {gcal_error_message}"
                            )
                            
                            return f"{REROUTE_COMPLETED_STATUS}|{gcal_error_message} Por favor, escolha outro horário ou data."

                    elif function_name == "ver_horarios_disponiveis":
                        data_YYYY_MM_DD = function_args.get("data") 

                        try:
                            dt_obj = datetime.strptime(data_YYYY_MM_DD, "%Y-%m-%d")
                            data_para_validacao = dt_obj.strftime("%d/%m/%Y")
                        except ValueError:
                            data_para_validacao = data_YYYY_MM_DD 

                        validacao_passada = validar_data_nao_passada(data_para_validacao)
                        if not validacao_passada.get('status') == 'SUCCESS': 
                            return "A data que você informou já passou. Por favor, escolha uma data futura."

                        validacao_domingo = validar_dia_nao_domingo(data_para_validacao)
                        if not validacao_domingo.get('status') == 'SUCCESS':
                            return "Não agendamos consultas aos domingos. Por favor, escolha outro dia."
                        
                        function_args['service'] = ServicesCalendar.get_service()
                        resultado_tool = ServicesCalendar.buscar_horarios_disponiveis(**function_args)

                        if resultado_tool.get("status") == "ERROR":
                            return resultado_tool.get('message')

                        available_slots = resultado_tool.get("available_slots", [])
                        data_formatada = data_para_validacao
                        
                        if not available_slots:
                            return (f"""{REROUTE_COMPLETED_STATUS}|Nenhum horário disponível em **{data_formatada}**.\n\nInforme outra data para verificar (AAAA-MM-DD).""")
                        else:
                            update_session_state(chat_id, registration_step=AGENT_DATE_CONFIRM) 
                            slots_str = "\n".join([f"  - {slot}" for slot in available_slots])
                            return (f"""Os Horários disponíveis em *{data_formatada}*:
{slots_str}
Qual horário deseja agendar? (Informe o horário no formato HH:MM)""")         

                    elif function_name == 'exibir_proximos_horarios_flex':
                        resultado_str = function_to_call(
                            service=ServicesCalendar.get_service(), 
                            chat_id=chat_id
                        )
                        if resultado_str.startswith("❌"): 
                            return f"{REROUTE_COMPLETED_STATUS}|{resultado_str}"
                          
                        update_session_state(chat_id, registration_step=AGENT_DATE_CONFIRM)
                        return resultado_str

            return resposta_ia
            
        except Exception as e:
            registrar_evento(
                cliente_id=chat_id,
                event_id='agent_critical_fail',
                tipo_metrica='agendamento',
                status='error_critico',
                detalhes=f"Falha CRÍTICA no agente date: {str(e)}"
            )
            logger.error(f"Erro CRÍTICO no Agent_date: {e}", exc_info=True)
            raise