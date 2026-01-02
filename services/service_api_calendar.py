import os
import datetime
from datetime import datetime, timedelta, timezone
import logging
import holidays
from googleapiclient.discovery import build
from google.oauth2 import service_account


logger = logging.getLogger(__name__)
BR_TIMEZONE = timezone(timedelta(hours=-3))
ID_AGENDA_ALVO = os.environ.get('ID_AGENDA')

SCOPES = ['https://www.googleapis.com/auth/calendar']

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'credentials_service.json')

class ToolException(Exception):
    """Exceção customizada para erros de ferramenta."""
    pass

def get_temporal_context():
    hoje = datetime.now()
    dias_semana_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    
    proximos_7_dias = ""
    for i in range(7):
        data_alvo = hoje + timedelta(days=i)
        nome_dia = dias_semana_pt[data_alvo.weekday()]
        data_iso = data_alvo.strftime("%Y-%m-%d")
        
        label = ""
        if i == 0: label = " (HOJE)"
        elif i == 1: label = " (AMANHÃ)"
        
        proximos_7_dias += f"- {nome_dia}{label}: {data_iso}\n"
        
    return proximos_7_dias

def validar_nao_feriado(data_str: str) -> dict:
    try:
        data_obj = datetime.strptime(data_str, "%d/%m/%Y").date()
        feriados_br = holidays.BR(state='SP') 
        
        if data_obj in feriados_br:
            nome_feriado = feriados_br.get(data_obj)
            return {
                "status": "FAILURE", 
                "message": f"A data escolhida é feriado ({nome_feriado}). Não teremos atendimento."
            }
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato de data inválido."}
    
def validar_dia_nao_domingo(data_str: str) -> dict:
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y")
        if data_consulta.weekday() == 6:
            return {"status": "FAILURE", "message": "Não fazemos agendamentos aos domingos. Por favor, escolha outro dia."}
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato de data inválido. Use DD/MM/AAAA."}

def validar_data_nao_passada(data_str: str) -> dict:
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y").date()
        hoje = datetime.now(timezone(timedelta(hours=-3))).date() 
        
        if data_consulta < hoje:
            return {"status": "FAILURE", "message": "A data informada já passou."}
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato de data inválido. Use DD/MM/AAAA."}

def gerar_horarios_disponiveis() -> list:
    horarios = []
    start_time = datetime.strptime("07:00", "%H:%M")
    end_time = datetime.strptime("20:00", "%H:%M")
    
    current_time = start_time
    while current_time < end_time:
        horarios.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=60)
    return horarios

def is_slot_busy(slot_time_str: str, busy_blocks: list, data: str, duration_minutos: int) -> bool:
    slot_start_dt = datetime.strptime(f"{data}T{slot_time_str}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
    slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutos)
    
    for block in busy_blocks:
        try:
            busy_start_dt = datetime.fromisoformat(block['start'])
            busy_end_dt = datetime.fromisoformat(block['end'])
        except ValueError:
            continue 
        if slot_start_dt < busy_end_dt and slot_end_dt > busy_start_dt:
            return True
    return False

def buscar_disponibilidade_escalonada(
    service, 
    limite_slots: int = 3, 
    duracao_minutos: int = 60,
    margens_dias: list[int] = None
) -> dict:
    """
    Busca os próximos slots livres usando a estratégia escalonada.
    """
    if margens_dias is None:
        margens_dias = [4, 10, 30] 
        
    hoje = datetime.now(BR_TIMEZONE).date()
    slots_sugeridos = []
    
    for margem in margens_dias:
        logging.info(f"Iniciando busca flexível: Margem de +{margem} dias (sem domingos).")
        for i in range(margem):
            data_atual = hoje + timedelta(days=i)
            if data_atual.weekday() == 6: 
                logging.debug(f"⏭️ Pulando {data_atual.strftime('%Y-%m-%d')} - É Domingo.")
                continue       
            
            data_str = data_atual.strftime("%Y-%m-%d")
            resultado = ServicesCalendar.buscar_horarios_disponiveis(
                data_str, 
                None,     
                duracao_minutos,
                service=service 
            )
            
            if resultado.get('status') != 'SUCCESS':
                continue
            
            for hora in resultado.get('available_slots', []):
                data_hora_iso = f"{data_str}T{hora}:00-03:00"
                try:
                    data_hr_obj = datetime.strptime(f"{data_str} {hora}", "%Y-%m-%d %H:%M")
                    data_hr_legivel = data_hr_obj.strftime("%d/%m - %H:%M")
                except ValueError:
                    continue

                slots_sugeridos.append({
                    'iso_time': data_hora_iso,
                    'legivel': data_hr_legivel
                })
                
                if len(slots_sugeridos) >= limite_slots:
                    return {
                        "status": "SUCCESS", 
                        "available_slots": slots_sugeridos
                    }

    if slots_sugeridos:
        return {"status": "SUCCESS", "available_slots": slots_sugeridos}
    
    return {
        "status": "SUCCESS",
        "available_slots": [],
        "message": "Nenhum horário disponível foi encontrado nas próximas semanas."
    }

class ServicesCalendar:
    service = None
    calendar_id = ID_AGENDA_ALVO 

    @classmethod
    def get_service(cls):
        """Sempre use este método para obter a conexão."""
        if cls.service is None:
            cls.inicializar_servico()
        return cls.service

    @classmethod
    def inicializar_servico(cls):
        if cls.service:
            return cls.service
        try:
            if not os.path.exists(SERVICE_ACCOUNT_FILE):
                logger.error(f"❌ Arquivo de credenciais não encontrado: {SERVICE_ACCOUNT_FILE}")
                return None

            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            cls.service = build('calendar', 'v3', credentials=creds)
            print("✅ Conectado ao Google Calendar via Service Account.")
            return cls.service
        except Exception as e:
            logger.error(f"❌ Erro fatal na autenticação: {e}")
            return None
        
    @classmethod    
    def buscar_eventos_do_dia(cls, calendar_id=None, data_inicio=None) -> list:
        """
        Método ajustado para ClassMethod para uso no Dashboard.
        """
        api_service = cls.get_service()
        if not api_service: return []

        if data_inicio is None:
            data_inicio = datetime.now(BR_TIMEZONE).replace(hour=0, minute=0, second=0)
        
        if isinstance(data_inicio, str):
            data_inicio = datetime.strptime(data_inicio, "%Y-%m-%d").replace(tzinfo=BR_TIMEZONE)

        id_final = calendar_id or cls.calendar_id or "primary"
        data_fim = data_inicio.replace(hour=23, minute=59, second=59)
        
        try:
            return api_service.events().list(
                calendarId=id_final,
                timeMin=data_inicio.isoformat(),
                timeMax=data_fim.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute().get('items', [])
        except Exception as e:
            logger.error(f"Erro ao buscar eventos: {e}")
            return []

    @classmethod
    def buscar_horarios_disponiveis(cls, data: str, calendar_id_arg: str = None, duracao_minutos: int = 60, **kwargs):
        """
        Busca horários livres no dia específico.
        """
        try:
            api_service = kwargs.get('service') or cls.get_service()
            
            if not api_service:
                return {"status": "ERROR", "message": "Sistema não autorizado no Google."}            

            id_final = calendar_id_arg or cls.calendar_id or 'primary'

            try:
                data_date_obj = datetime.strptime(data, "%Y-%m-%d").date()
            except ValueError:
                return {"status": "ERROR", "message": f"Formato inválido: {data}"}

            hoje = datetime.now(BR_TIMEZONE).date()
            feriados_br = holidays.BR(state='SP')
            if data_date_obj in feriados_br:
                return {"status": "ERROR", "message": f"Feriado."}

            time_min = f'{data}T07:00:00-03:00'
            time_max = f'{data}T20:00:00-03:00'
            
            query_body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": id_final}]
            }
            
            freebusy_response = api_service.freebusy().query(body=query_body).execute()
            busy_blocks = freebusy_response.get('calendars', {}).get(id_final, {}).get('busy', [])
            
            horarios = gerar_horarios_disponiveis() 
            livres = []
            now_with_margin = datetime.now(BR_TIMEZONE) + timedelta(minutes=30)
            
            for h in horarios:
                is_busy = is_slot_busy(h, busy_blocks, data, duracao_minutos)
                if not is_busy:
                    if data_date_obj == hoje:
                        slot_dt = datetime.strptime(f"{data}T{h}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
                        if slot_dt >= now_with_margin:
                            livres.append(h)
                    else:
                        livres.append(h)

            return {"status": "SUCCESS", "available_slots": livres}
            
        except Exception as e:
            logging.error(f"Erro em buscar_horarios_disponiveis: {e}")
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def criar_evento(service, calendar_id_arg='primary', start_time_str=None, chat_id=None, name=None, summary=None, time_zone='America/Sao_Paulo'):
        """
        Cria o evento. 
        """
        if not service:
            service = ServicesCalendar.get_service()
            if not service:
                return {"status": "ERROR", "message": "Service não inicializado."}

        try:
            start_dt = datetime.fromisoformat(start_time_str)
            data_str = start_dt.strftime("%Y-%m-%d")
            hora_str = start_dt.strftime("%H:%M")

            disponiveis = ServicesCalendar.buscar_horarios_disponiveis(
                data=data_str,
                duracao_minutos=60,
                service=service
            )
            
            if disponiveis.get('status') == 'ERROR' or hora_str not in disponiveis.get('available_slots', []):
                return {"status": "ERROR", "message": f"O horário {hora_str} não está mais disponível."}
            
            end_dt = start_dt + timedelta(minutes=60)
            final_summary = f"CONSUL Nome:{name} - Cliente ID:{chat_id}"

            event_body = {
                'summary': final_summary, 
                'start': {'dateTime': start_time_str, 'timeZone': time_zone},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': time_zone},
                'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 10}]}
            }

            event = service.events().insert(calendarId=calendar_id_arg, body=event_body).execute()
            
            return {
                "status": "SUCCESS", 
                "event_link": event.get('htmlLink'), 
                "event_id": event.get('id'),
                "start_time": start_time_str
            }
            
        except Exception as e:
            logging.error(f"Erro ao criar evento: {e}")
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def deletar_evento(service, calendar_id_arg='primary', event_id=None):
        if not service: 
            service = ServicesCalendar.get_service()
            if not service: return {"status": "ERROR", "message": "Service off."}
            
        try:
            service.events().delete(calendarId=calendar_id_arg, eventId=event_id).execute()
            return {"status": "SUCCESS", "message": "Cancelado."}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def buscar_proximos_disponiveis(service=None, limite_slots: int = 3, duracao_minutos: int = 60, **kwargs) -> dict:
        """
        Wrapper para a Tool da IA.
        """
        api_service = service or kwargs.get('service') or ServicesCalendar.get_service()
        return buscar_disponibilidade_escalonada(
            service=api_service, 
            limite_slots=limite_slots, 
            duracao_minutos=duracao_minutos
        )

    @staticmethod
    def exibir_proximos_horarios_flex(service=None, chat_id: str = None, **kwargs) -> str:
        """
        Tool final chamada pela IA.
        """
        api_service = service or kwargs.get('service') or ServicesCalendar.get_service()
        
        resultado_tool = ServicesCalendar.buscar_proximos_disponiveis(
            service=api_service, 
            limite_slots=11, 
            duracao_minutos=60 
        )
        
        try:
            if resultado_tool.get("status") == "SUCCESS":
                slots = resultado_tool.get("available_slots", [])
                if not slots:
                    return "❌ Sem horários livres nas próximas semanas."

                slots_agrupados = {}
                for slot in slots:
                    parts = slot['legivel'].split(' - ')
                    if len(parts) == 2:
                        dia, hora = parts[0], parts[1]
                        if dia not in slots_agrupados: slots_agrupados[dia] = []
                        slots_agrupados[dia].append(hora)
                
                output = []
                for dia, horas in slots_agrupados.items():
                    output.append(f" {dia}: {', '.join(horas)}")
                
                return f"Encontrei estes horários:\n\n" + "\n".join(output) + "\n\nQual você prefere?"
            
            return f"Erro ao buscar: {resultado_tool.get('message')}"

        except Exception as e:
            logger.error(f"Erro CRÍTICO Tool Date: {e}", exc_info=True)
            raise