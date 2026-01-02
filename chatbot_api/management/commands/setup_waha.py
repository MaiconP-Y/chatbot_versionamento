import os
import time
import logging
from django.core.management.base import BaseCommand
logger = logging.getLogger(__name__)

from chatbot_api.models import SystemControl

class Command(BaseCommand):
    def handle(self, *args, **options):
        self.stdout.write("🔐 Verificando autorização de inicialização...")
        trava, _ = SystemControl.objects.get_or_create(chave="waha_pode_iniciar")
        
        if not trava.liberado:
            self.stderr.write(self.style.WARNING(
                "🛑 WAHA BLOQUEADO: Aguardando primeira conexão manual via QR Code no painel."
            ))
            return
        
        self.stdout.write(self.style.SUCCESS("✅ Autorização confirmada. Configurando WAHA..."))
        from services.waha_api import Waha
        waha = Waha()
        hmac_key = os.environ.get("WEBHOOK_HMAC_SECRET")
        max_retries = 15 
        delay_seconds = 5
        self.stdout.write(self.style.WARNING(f"⏳ A aguardar que o WAHA suba (max {max_retries * delay_seconds}s)..."))
        time.sleep(10) 

        for attempt in range(1, max_retries + 1):
            try:
                success = waha.start_session_with_hmac(hmac_key)
                if success:
                    self.stdout.write(self.style.SUCCESS(f"✅ WAHA configurado com sucesso na tentativa {attempt}!"))
                    return
            except Exception as e:
                self.stdout.write(f"⚠️ WAHA ainda não está pronto... ({e})")
            
            self.stdout.write(f"Attempt {attempt}/{max_retries}: Aguardando mais {delay_seconds}s...")
            time.sleep(delay_seconds)
        
        self.stderr.write(self.style.ERROR("❌ Falha crítica: WAHA não ficou disponível a tempo."))
        exit(1)