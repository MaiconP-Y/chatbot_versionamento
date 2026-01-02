#!/bin/bash
set -e

# Função para aguardar o Postgres (evita erros de conexão iniciais)
wait_for_db() {
  echo "⏳ Verificando se o banco de dados está pronto..."
  until nc -z $DATABASE_HOST $DATABASE_PORT; do
    sleep 1
  done
  echo "✅ Banco de dados online!"
}

# Se for o container web (Django), ele faz o trabalho pesado
if [[ "$*" == *"uvicorn"* ]]; then
    wait_for_db
    
    echo "🚀 [WEB] Executando migrações..."
    python manage.py migrate --noinput

    echo "👤 [WEB] Criando Superusuário..."
    python manage.py createsuperuser --noinput || echo "ℹ️ Usuário já existe, pulando..."

    echo "📦 [WEB] Coletando estáticos..."
    python manage.py collectstatic --noinput

    echo "🔐 [WEB] Configurando WAHA..."
    # Agora roda isolado, sem concorrência externa
    python manage.py setup_waha
fi

# Se for worker, apenas avisa e segue
if [[ "$*" == *"celery"* || "$*" == *"whatsapp_worker.py"* ]]; then
    echo "👷 [WORKER] Iniciando serviço..."
fi

echo "🎬 Executando comando: $@"
exec "$@"