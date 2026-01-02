FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1 

WORKDIR /app

# Instala dependências do sistema (incluindo netcat para healthchecks se necessário)

RUN useradd -m appuser && \
    mkdir -p /app/staticfiles_dist && \
    chown -R appuser:appuser /app
    
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    python3-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip 
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/
RUN chown -R appuser:appuser /app
RUN chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 8000

# Define o entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# O CMD agora é apenas o comando padrão que será passado como "$@" para o entrypoint
CMD ["uvicorn", "chatbot.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]