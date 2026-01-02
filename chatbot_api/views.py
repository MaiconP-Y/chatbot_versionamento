import logging
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from chatbot_api.models import UserRegister
from django.db import transaction, IntegrityError
from chatbot_api.models import LogMetrica
from services.redis_client import remove_from_blacklist, get_all_blacklisted_chat_ids, add_to_blacklist, delete_history, delete_user_profile_cache, delete_session_state
from chatbot_api.metrics import registrar_evento

import requests
from django.http import HttpResponse
from django.contrib.auth.decorators import user_passes_test

import os
from django.conf import settings
from google_auth_oauthlib.flow import Flow
from django.shortcuts import redirect
import re

logger = logging.getLogger(__name__)

def get_waha_status_logic():
    """Lógica pura para checar o status do WAHA"""
    try:
        url = "http://waha:3000/api/sessions/default"
        headers = {'X-Api-Key': os.environ.get("WAHA_API_KEY")}
        response = requests.get(url, headers=headers, timeout=2)
        if response.status_code == 200:
            from chatbot_api.models import SystemControl
            SystemControl.objects.update_or_create(
                chave="waha_pode_iniciar", 
                defaults={'liberado': True}
            )
            return response.json().get("status").upper()
        return "OFFLINE"
    except Exception:
        return "OFFLINE"

CALLBACK_URL = "http://127.0.0.1/control-quiro/auth/google/callback/"

def google_auth_redirect(request):
    """Passo 1: Redireciona o admin para o Google"""
    client_secrets_file = os.path.join(settings.BASE_DIR, 'client_secret.json')
    
    flow = Flow.from_client_secrets_file(
        client_secrets_file,
        scopes=['https://www.googleapis.com/auth/calendar'],
        redirect_uri=CALLBACK_URL
    )
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    request.session['oauth_state'] = state
    return redirect(auth_url)

@api_view(['POST'])
@transaction.atomic
def log_metric(request):
    """
    Endpoint HTTP para registrar logs de métrica de forma atômica no PostgreSQL.
    Usado pelo Worker de IA.
    """
    data = request.data
    required_fields = ['cliente_id', 'event_id', 'tipo_metrica']
    if not all(field in data for field in required_fields):
        logger.error(f"❌ Tentativa de log de métrica inválida: {data}")
        return Response(
            {"status": "FAILURE", "message": "Campos obrigatórios ausentes."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        LogMetrica.registrar_evento(
            cliente_id=data.get('cliente_id'),
            event_id=data.get('event_id'),
            tipo_metrica=data.get('tipo_metrica'),
            status=data.get('status', 'success'), 
            detalhes=data.get('detalhes', ''),
        )
        return Response({"status": "SUCCESS", "message": "Métrica registrada."}, status=status.HTTP_201_CREATED)
        
    except IntegrityError as e:
        logger.warning(f"⚠️ Erro de Integridade ao registrar métrica (Rollback): {e}")
        return Response({"status": "FAILURE", "message": "Erro de integridade do DB."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"❌ Erro CRÍTICO ao registrar métrica: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def get_user_data(request, chat_id):
    """
    Endpoint de API para retornar dados de registro de um usuário.
    ✅ AGORA: Implementa o filtro de datas futuras e a formatação (replicando a lógica antiga).
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        consultas = []
        agora = timezone.now()

        if user.appointment1_gcal_id and user.appointment1_datetime and user.appointment1_datetime >= agora:
            local_dt1 = timezone.localtime(user.appointment1_datetime)
            
            consultas.append({
                "appointment_number": 1,
                "data": local_dt1.strftime("%d/%m/%Y"),
                "hora": local_dt1.strftime("%H:%M"),
                "slot": 1,
                "gcal_id": user.appointment1_gcal_id,
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        if user.appointment2_gcal_id and user.appointment2_datetime and user.appointment2_datetime >= agora:
            local_dt2 = timezone.localtime(user.appointment2_datetime)

            consultas.append({
                "appointment_number": 2,
                "data": local_dt2.strftime("%d/%m/%Y"),
                "hora": local_dt2.strftime("%H:%M"),
                "slot": 2,
                "gcal_id": user.appointment2_gcal_id,
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        consultas.sort(key=lambda x: datetime.strptime(f"{x['data']} {x['hora']}", "%d/%m/%Y %H:%M"))
        
        response_data = {
            "status": "SUCCESS",
            "chat_id": user.chat_id,
            "username": user.username,
            "appointments": consultas
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao buscar dados de usuário no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@transaction.atomic
def salvar_agendamento_transacional(request):
    """
    Endpoint de API que recebe a requisição do Worker de IA e executa a 
    sua lógica de slots e transação atômica.
    """
    
    chat_id = request.data.get('chat_id')
    google_event_id = request.data.get('google_event_id')
    start_time_iso = request.data.get('start_time_iso')
    
    if not all([chat_id, google_event_id, start_time_iso]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        user = UserRegister.objects.select_for_update().get(chat_id=chat_id) 
        new_datetime = datetime.fromisoformat(start_time_iso)
        agora = timezone.now()
        
        is_slot1_free = not user.appointment1_gcal_id or (user.appointment1_datetime and user.appointment1_datetime < agora)
        
        if is_slot1_free:
            user.appointment1_datetime = new_datetime
            user.appointment1_gcal_id = google_event_id
            user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
            logger.info(f"✅ Agendamento salvo no slot 1 (BaaS) - Cliente: {chat_id}")
            response_data = {"status": "SUCCESS", "slot": 1, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)

        is_slot2_free = not user.appointment2_gcal_id or (user.appointment2_datetime and user.appointment2_datetime < agora)
        
        if is_slot2_free:
            user.appointment2_datetime = new_datetime
            user.appointment2_gcal_id = google_event_id
            user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id']) 
            logger.info(f"✅ Agendamento salvo no slot 2 (BaaS) - Cliente: {chat_id}")
            response_data = {"status": "SUCCESS", "slot": 2, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response({"status": "FAILURE", "message": "Limite de agendamentos atingido. Você pode ter no máximo 2 consultas ativas."}, 
                            status=status.HTTP_409_CONFLICT)
                            
    except UserRegister.DoesNotExist:
        return Response({"status": "FAILURE", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        logger.error(f"❌ Erro grave ao salvar agendamento no BaaS: {e}")
        # Retorna erro de servidor para o Worker de IA
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST']) 
def register_user(request):
    """
    Endpoint para criar um novo usuário.
    O worker faz um POST aqui para registrar o nome fornecido.
    """
    try:
        with transaction.atomic():
            data = request.data
            chat_id = data.get('chat_id')
            username = data.get('name') 
            
            if not chat_id or not username:
                return Response({"status": "FAILURE", "message": "Campos 'chat_id' e 'name' são obrigatórios."}, 
                                status=status.HTTP_400_BAD_REQUEST)

            user = UserRegister.objects.create(chat_id=chat_id, username=username)

            return Response({
                "status": "SUCCESS", 
                "message": "Usuário registrado com sucesso.", 
                "username": user.username 
            }, status=status.HTTP_201_CREATED)
            
    except IntegrityError:
        return Response({"status": "FAILURE", "message": "Usuário já existe."}, 
                        status=status.HTTP_409_CONFLICT) 
        
    except Exception as e:
        logger.error(f"❌ Erro ao registrar usuário: {e}", exc_info=True)
        return Response({"status": "ERROR", "message": "Erro interno do servidor."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def cancel_appointment_transacional(request):
    """
    Endpoint de API para limpar o slot de agendamento no DB (limpa o slot).
    Garante a atomicidade e o lock de linha.
    """
    chat_id = request.data.get('chat_id')
    numero_consulta = request.data.get('numero_consulta')

    if not all([chat_id, numero_consulta]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, 
                        status=status.HTTP_400_BAD_REQUEST)
    
    try:
        numero_consulta = int(numero_consulta)
    except ValueError:
         return Response({"status": "ERROR", "message": "numero_consulta deve ser um inteiro."}, 
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            user = UserRegister.objects.select_for_update().get(chat_id=chat_id)
            
            db_slot_cleared = False
            
            if numero_consulta == 1 and user.appointment1_gcal_id:
                user.appointment1_datetime = None
                user.appointment1_gcal_id = None
                user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
                db_slot_cleared = True
                
            elif numero_consulta == 2 and user.appointment2_gcal_id:
                user.appointment2_datetime = None
                user.appointment2_gcal_id = None
                user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id'])
                db_slot_cleared = True
                
            if not db_slot_cleared:
                return Response({"status": "FAILURE", "message": f"Não encontrei agendamento ativo no slot {numero_consulta} para limpar."}, 
                                status=status.HTTP_404_NOT_FOUND)

            logger.info(f"✅ Slot {numero_consulta} LIMPO no DB (BaaS) - Cliente: {chat_id}")
            
            return Response({"status": "SUCCESS", "message": "Slot limpo no banco de dados."}, 
                            status=status.HTTP_200_OK)

    except UserRegister.DoesNotExist:
        return Response({"status": "FAILURE", "message": "Usuário não encontrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erro grave ao limpar slot {numero_consulta} no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def list_active_appointments(request, chat_id):
    """
    Endpoint de API para retornar uma lista formatada de agendamentos ATIVOS.
    A lógica de 'ativo' e a formatação são executadas AQUI no BaaS.
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        lista_consultas = []
        agora = timezone.now() 

        if user.appointment1_datetime and user.appointment1_datetime > agora:
            lista_consultas.append({
                "appointment_number": 1, 
                "data": user.appointment1_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment1_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment1_gcal_id,
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        if user.appointment2_datetime and user.appointment2_datetime > agora:
            lista_consultas.append({
                "appointment_number": 2, 
                "data": user.appointment2_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment2_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment2_gcal_id,
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        return Response({"status": "SUCCESS", "appointments": lista_consultas}, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "appointments": []}, status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao listar agendamentos ativos no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
@api_view(['POST'])
@transaction.atomic
def cleanup_expired_appointments_view(request):
    """
    Limpa agendamentos que já ocorreram há mais de 2 horas.
    Ex: Se agora é 14:00, limpa tudo agendado para antes das 12:00.
    """
    agora_utc = timezone.now()
    data_limite = agora_utc - timedelta(hours=2)

    logger.info(f"🧹 Iniciando limpeza. Agora(UTC): {agora_utc} | Cortar agendamentos anteriores a: {data_limite}")

    result_slot1 = UserRegister.objects.filter(
        appointment1_datetime__lt=data_limite,
        appointment1_datetime__isnull=False 
    ).update(
        appointment1_datetime=None,
        appointment1_gcal_id=None
    )

    result_slot2 = UserRegister.objects.filter(
        appointment2_datetime__lt=data_limite,
        appointment2_datetime__isnull=False
    ).update(
        appointment2_datetime=None,
        appointment2_gcal_id=None
    )

    total_limpos = result_slot1 + result_slot2
    
    if total_limpos > 0:
        logger.info(f"✅ Limpeza concluída. {total_limpos} slots antigos foram liberados.")
    else:
        logger.info(f"ℹ️ Limpeza executada, mas nenhum agendamento expirado (anterior a {data_limite}) foi encontrado.")
    
    return Response({"status": "SUCCESS", "slots_limpos": total_limpos}, status=status.HTTP_200_OK)    

from django.shortcuts import render
from django.contrib.auth.decorators import user_passes_test, login_required
from services.redis_client import get_dashboard_logs, get_dashboard_logs

def is_superuser(user):
    return user.is_active and user.is_superuser

import re

@login_required
@user_passes_test(is_superuser)
def admin_view(request):
    from services.service_api_calendar import ServicesCalendar

    path_json = os.path.join(settings.BASE_DIR, 'credentials_service.json')
    google_ready = os.path.exists(path_json)
    
    agenda_google = []
    if google_ready:
        try:
            service = ServicesCalendar.get_service()
            if service:
                hoje_dt = timezone.localtime(timezone.now())
                instancia_calendario = ServicesCalendar()
                eventos = instancia_calendario.buscar_eventos_do_dia(calendar_id=None, data_inicio=hoje_dt)
                
                for ev in eventos:
                    summary = ev.get('summary', '')
                    nome = re.search(r"Nome:(.*?) -", summary).group(1).strip() if "Nome:" in summary else "N/A"
                    whatsapp = summary.split("ID:")[-1].split('@')[0].strip() if "ID:" in summary else "N/A"
                    start = ev.get('start', {}).get('dateTime', ev.get('start', {}).get('date', ''))
                    horario = start[11:16] if 'T' in start else "Dia Todo"
                    agenda_google.append({'horario': horario, 'nome': nome, 'whatsapp': whatsapp})
        except Exception as e:
            logger.error(f"Erro agenda: {e}")

    status_waha = get_waha_status_logic() 

    return render(request, 'admin_interface.html', {
        'google_ready': google_ready,
        'status_waha': status_waha,
        'usuarios': UserRegister.objects.all().order_by('username'),
        'agenda_google': sorted(agenda_google, key=lambda x: x['horario']),
        'logs_diarios': get_dashboard_logs()['logs'],
        'blacklisted_ids': sorted(list(get_all_blacklisted_chat_ids())),
    })

@api_view(['POST'])
@user_passes_test(is_superuser)
def delete_users_api(request):
    """
    Remove múltiplos usuários do PostgreSQL de forma atômica.
    """
    chat_ids = request.data.get('chat_ids', [])

    if not chat_ids:
        return Response({
            "status": "FAILURE", 
            "message": "Nenhum usuário selecionado."
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            quantidade, _ = UserRegister.objects.filter(chat_id__in=chat_ids).delete()
            registrar_evento(
                cliente_id="ADMIN",
                event_id="admin_manual_delete",
                tipo_metrica="config_control",
                status="success",
                detalhes=f"Removidos {quantidade} usuários manualmente pelo painel."
            )

        return Response({
            "status": "SUCCESS", 
            "message": f"{quantidade} usuário(s) removido(s) com sucesso."
        })

    except Exception as e:
        logger.error(f"Erro ao deletar usuários: {e}")
        return Response({
            "status": "FAILURE", 
            "message": "Erro interno ao processar a deleção."
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['POST'])
@user_passes_test(is_superuser)
@transaction.atomic
def deactivate_handoff(request):
    """
    Desativa o Handover (Retorno para IA).
    Suporta tanto um único 'chat_id' quanto uma lista 'chat_ids'.
    """
    chat_ids = request.data.get('chat_ids', [])
    single_id = request.data.get('chat_id')
    ids_para_processar = []
    if chat_ids:
        ids_para_processar = chat_ids if isinstance(chat_ids, list) else [chat_ids]
    elif single_id:
        ids_para_processar = [single_id]
    if not ids_para_processar:
        return Response({
            "status": "FAILURE", 
            "message": "Nenhum ID de chat foi fornecido."
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        count = 0
        for cid in ids_para_processar:
            if cid:
                remove_from_blacklist(cid) 
                registrar_evento(
                    cliente_id=cid,
                    event_id='admin_action_handoff',
                    tipo_metrica='atendimento_control',
                    status='success',
                    detalhes="HANDOVER FINALIZADO: Bot reativado via painel admin."
                )
                count += 1

        return Response({
            "status": "SUCCESS", 
            "message": f"{count} bot(s) liberado(s) com sucesso."
        })

    except Exception as e:
        logger.error(f"Erro ao processar deactivate_handoff: {e}")
        return Response({
            "status": "FAILURE", 
            "message": "Erro interno ao comunicar com Redis/Banco."
        }, status=500)
    
@api_view(['POST'])
@user_passes_test(is_superuser)
def assumir_conversa_api(request):
    chat_id = request.data.get('chat_id')
    if not chat_id:
        return Response({"status": "FAILURE", "message": "ID ausente."}, status=400)

    try:
        add_to_blacklist(chat_id)       
        delete_user_profile_cache(chat_id)
        delete_session_state(chat_id)
        delete_history(chat_id) 
        
        registrar_evento(
            cliente_id=chat_id, event_id="admin_intervencao_manual",
            tipo_metrica="atendimento_control", status="success",
            detalhes="Admin assumiu a conversa. Histórico e Cache limpos."
        )
        
        return Response({"status": "SUCCESS", "message": f"Conversa com {chat_id} assumida. Bot pausado."})
    except Exception as e:
        return Response({"status": "FAILURE", "message": str(e)}, status=500)

@api_view(['POST'])
@user_passes_test(is_superuser)
def emergency_redis_flush(request):
    import redis

    try:
        host = os.environ.get('REDIS_HOST')
        port = os.environ.get('REDIS_PORT')
        db = os.environ.get('REDIS_DB')
        raw_url = f"redis://{host}:{port}/{db}"

        logger.info(f"🔌 Executando Flush de Emergência no Redis: {raw_url}")
        r_client = redis.from_url(raw_url)
        r_client.flushdb()
        
        return Response({
            "status": "SUCCESS", 
            "message": f"⚠️ Sistema Reiniciado! DB {db} do Redis foi limpo."
        })

    except Exception as e:
        logger.error(f"❌ Erro Crítico no Flush: {str(e)}", exc_info=True)
        return Response({
            "status": "FAILURE", 
            "message": f"Erro na conexão Redis ({host}): {str(e)}"
        }, status=500)
    

from services.waha_api import Waha
import time
def waha_qr_proxy(request):
    waha = Waha()
    hmac_key = os.environ.get("WEBHOOK_HMAC_SECRET")
    waha.start_session_with_hmac(hmac_key)
    time.sleep(10)
    url = "http://waha:3000/api/default/auth/qr?format=image"
    headers = {"X-Api-Key": os.environ.get("WAHA_API_KEY")}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return HttpResponse(response.content, content_type="image/png")
        return HttpResponse("Aguardando geração do QR...", status=404)
    except:
        return HttpResponse("Erro ao conectar com WAHA", status=500)
    
@api_view(['GET'])
def check_waha_status(request):
    status = get_waha_status_logic()
    return Response({"status": status})
    