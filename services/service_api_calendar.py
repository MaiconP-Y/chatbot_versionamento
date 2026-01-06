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

# --- Funções Auxiliares de Formatação ---

def obter_label_data(data_iso: str) -> str:
    """
    Retorna uma string amigável: 'Hoje (06/01)', 'Amanhã (07/01)' ou 'Quarta-feira (08/01)'.
    Usa o weekday() do Python para precisão total.
    """
    dt_obj = datetime.strptime(data_iso, "%Y-%m-%d").date()
    hoje = datetime.now(BR_TIMEZONE).date()
    dias_semana_pt = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    
    if dt_obj == hoje:
        label = "Hoje"
    elif dt_obj == hoje + timedelta(days=1):
        label = "Amanhã"
    else:
        label = dias_semana_pt[dt_obj.weekday()]
        
    return f"{label} ({dt_obj.strftime('%d/%m')})"

# --- Funções de contexto e validação ---

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
            return {"status": "FAILURE", "message": f"A data escolhida é feriado ({nome_feriado})."}
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato inválido."}

def validar_dia_nao_domingo(data_str: str) -> dict:
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y")
        if data_consulta.weekday() == 6:
            return {"status": "FAILURE", "message": "Não atendemos aos domingos."}
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato inválido."}

def validar_data_nao_passada(data_str: str) -> dict:
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y").date()
        hoje = datetime.now(BR_TIMEZONE).date() 
        if data_consulta < hoje:
            return {"status": "FAILURE", "message": "A data já passou."}
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato inválido."}

def gerar_horarios_disponiveis() -> list:
    horarios = []
    current_time = datetime.strptime("07:00", "%H:%M")
    end_time = datetime.strptime("20:00", "%H:%M")
    while current_time < end_time:
        horarios.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=60)
    return horarios

def is_slot_busy(slot_time_str: str, busy_blocks: list, data: str, duration_minutos: int) -> bool:
    slot_start_dt = datetime.strptime(f"{data}T{slot_time_str}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
    slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutos)
    for block in busy_blocks:
        try:
            busy_start_dt = datetime.fromisoformat(block['start'].replace('Z', '+00:00'))
            busy_end_dt = datetime.fromisoformat(block['end'].replace('Z', '+00:00'))
        except ValueError: continue 
        if slot_start_dt < busy_end_dt and slot_end_dt > busy_start_dt:
            return True
    return False

def buscar_disponibilidade_escalonada(service, limite_slots: int = 11, duracao_minutos: int = 60, margens_dias: list[int] = None) -> dict:
    hoje = datetime.now(BR_TIMEZONE).date()
    slots_sugeridos = []
    dias_tentados = 0
    MAX_DIAS_LIMITE = 90
    while len(slots_sugeridos) < limite_slots and dias_tentados < MAX_DIAS_LIMITE:
        data_atual = hoje + timedelta(days=dias_tentados)
        dias_tentados += 1
        if data_atual.weekday() == 6: continue       
        data_str = data_atual.strftime("%Y-%m-%d")
        resultado = ServicesCalendar.buscar_horarios_disponiveis(data_str, None, duracao_minutos, service=service)
        if resultado.get('status') != 'SUCCESS': continue
        
        header_dia = obter_label_data(data_str)

        for hora in resultado.get('available_slots', []):
            slots_sugeridos.append({
                'iso_time': f"{data_str}T{hora}:00-03:00",
                'legivel': f"{header_dia} - {hora}"
            })
            if len(slots_sugeridos) >= limite_slots:
                return {"status": "SUCCESS", "available_slots": slots_sugeridos}
    return {"status": "SUCCESS", "available_slots": slots_sugeridos}

class ServicesCalendar:
    service = None
    calendar_id = ID_AGENDA_ALVO 

    @classmethod
    def get_service(cls):
        if cls.service is None: cls.inicializar_servico()
        return cls.service

    @classmethod
    def inicializar_servico(cls):
        if cls.service: return cls.service
        try:
            creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
            cls.service = build('calendar', 'v3', credentials=creds)
            return cls.service
        except Exception as e:
            logger.error(f"Erro na autenticação: {e}")
            return None

    @classmethod
    def buscar_eventos_do_dia(cls, data: str = None, calendar_id_arg: str = None, **kwargs):
        """
        Retorna os eventos brutos do Google para a interface Admin. 
        Se data for None, assume hoje (evita erro no Admin).
        """
        try:
            api_service = kwargs.get('service') or cls.get_service()
            id_final = calendar_id_arg or cls.calendar_id or 'primary'
            
            if data is None:
                data_dt = datetime.now(BR_TIMEZONE).date()
            else:
                data_dt = datetime.strptime(data, "%Y-%m-%d").date()

            time_min = f"{data_dt}T00:00:00-03:00"
            time_max = f"{data_dt}T23:59:59-03:00"
            
            events_result = api_service.events().list(
                calendarId=id_final,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            # Para manter compatibilidade com o double-check do criar_evento
            # retornamos também os slots livres se for chamado com duracao_minutos
            if 'duracao_minutos' in kwargs:
                return cls.buscar_horarios_disponiveis(data=str(data_dt), calendar_id_arg=id_final, **kwargs)

            return events_result.get('items', [])
        except Exception as e:
            logger.error(f"Erro ao buscar eventos: {e}")
            return []

    @classmethod
    def buscar_horarios_disponiveis(cls, data: str, calendar_id_arg: str = None, duracao_minutos: int = 60, **kwargs):
        """
        Lógica de busca de slots livres com label de dia da semana.
        """
        try:
            api_service = kwargs.get('service') or cls.get_service()
            id_final = calendar_id_arg or cls.calendar_id or 'primary'
            data_date_obj = datetime.strptime(data, "%Y-%m-%d").date()
            hoje = datetime.now(BR_TIMEZONE).date()
            
            dia_formatado = obter_label_data(data)

            query_body = {
                "timeMin": f'{data}T07:00:00-03:00',
                "timeMax": f'{data}T20:00:00-03:00',
                "items": [{"id": id_final}]
            }
            freebusy_response = api_service.freebusy().query(body=query_body).execute()
            busy_blocks = freebusy_response.get('calendars', {}).get(id_final, {}).get('busy', [])
            
            horarios = gerar_horarios_disponiveis() 
            livres = []
            now_with_margin = datetime.now(BR_TIMEZONE) + timedelta(minutes=30)
            
            for h in horarios:
                if not is_slot_busy(h, busy_blocks, data, duracao_minutos):
                    if data_date_obj == hoje:
                        slot_dt = datetime.strptime(f"{data}T{h}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
                        if slot_dt >= now_with_margin: livres.append(h)
                    else: livres.append(h)

            return {"status": "SUCCESS", "available_slots": livres, "dia_formatado": dia_formatado}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def criar_evento(service, calendar_id_arg='primary', start_time_str=None, chat_id=None, name=None, summary=None, time_zone='America/Sao_Paulo'):
        if not service:
            service = ServicesCalendar.get_service()
            if not service: return {"status": "ERROR", "message": "Service off."}

        try:
            start_dt = datetime.fromisoformat(start_time_str)
            data_str = start_dt.strftime("%Y-%m-%d")
            hora_str = start_dt.strftime("%H:%M")

            # Double-check usando a lógica de disponibilidade
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
            logger.error(f"Erro ao criar evento: {e}")
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def deletar_evento(service, calendar_id_arg='primary', event_id=None):
        if not service: service = ServicesCalendar.get_service()
        try:
            service.events().delete(calendarId=calendar_id_arg, eventId=event_id).execute()
            return {"status": "SUCCESS", "message": "Cancelado."}
        except Exception as e:
            return {"status": "ERROR", "message": str(e)}

    @staticmethod
    def buscar_proximos_disponiveis(service=None, limite_slots: int = 3, duracao_minutos: int = 60, **kwargs) -> dict:
        api_service = service or kwargs.get('service') or ServicesCalendar.get_service()
        return buscar_disponibilidade_escalonada(service=api_service, limite_slots=limite_slots, duracao_minutos=duracao_minutos)

    @staticmethod
    def exibir_proximos_horarios_flex(service=None, chat_id: str = None, **kwargs) -> str:
        api_service = service or kwargs.get('service') or ServicesCalendar.get_service()
        resultado_tool = ServicesCalendar.buscar_proximos_disponiveis(api_service, limite_slots=11)
    
        if resultado_tool.get("status") == "SUCCESS":
            slots = resultado_tool.get("available_slots", [])
            if not slots: return "❌ Não encontrei horários disponíveis para as próximas semanas."

            slots_agrupados = {}
            for slot in slots:
                parts = slot['legivel'].split(' - ')
                dia_label, hora = parts[0], parts[1]
                if dia_label not in slots_agrupados: slots_agrupados[dia_label] = []
                slots_agrupados[dia_label].append(hora)
            
            output = ["*Horários disponíveis:*\n"]
            for dia, horas in slots_agrupados.items():
                output.append(f"• *{dia}*: {', '.join(horas)}")
            
            return "\n".join(output) + "\n\nQual desses horários fica melhor para você?"
        return "Erro ao buscar horários."