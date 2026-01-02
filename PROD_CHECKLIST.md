CHECKLIST DE PRODUÇÃO FINAL (Hostinger + Cloudflare)
====================================================
OTIMIZAR PROMPTS, calendar
### 1\. Preparação da Imagem (Build Time)

*   \[ \] **Dockerfile**: Garantir que o `USER appuser` está definido e as pastas têm as permissões corretas.
    
*   \[ \] **Apps.py**: Confirmar que o método `ready()` está vazio para evitar execuções duplicadas.
    
*   \[ \] **Imutabilidade**: Garantir que o código é copiado no build (`COPY . /app`) e não montado via volume.
    

### 2\. Infraestrutura e Rede (Docker Compose)

*   \[ \] **Volumes**: Remover volumes de código (`- .:/app` ou `- .:/usr/src/app`). Manter apenas volumes de dados (`postgres_data`, `staticfiles_volume`, etc).
    
*   \[ \] **Django Entrypoint**: Confirmar a sequência no `command`: `migrate` -> `collectstatic` -> `setup_waha` -> `uvicorn`.
    
*   \[ \] **Portas**: No `docker-compose.yml`, o serviço `nginx` deve expor a porta `"80:80"`. O Django **não** deve expor portas para o exterior.
    

### 3\. Segurança e Chaves (Gerar na VPS)

*   \[ \] **DJANGO\_SECRET\_KEY**: Gerar uma nova chave única para produção.
    
*   \[ \] **WAHA\_API\_KEY**: Definir uma chave forte para proteger a API do WhatsApp.
    
*   \[ \] **WEBHOOK\_HMAC\_SECRET**: Definir o segredo para validação das mensagens vindas do WAHA.
    
*   \[ \] **GROQ\_API\_KEY**: Inserir a chave de produção da API de IA.
    

### 4\. Configuração Cloudflare (O teu escudo Grátis)

1.  **Adicionar Site**: No painel Cloudflare, adiciona o teu domínio (Plano Free).
    
2.  **Mudar Nameservers**: No painel da **Hostinger**, substitui os DNS originais pelos fornecidos pela Cloudflare.
    
3.  **Registos DNS**:
    
    *   **Tipo A**: Nome `@`, Conteúdo `IP_DA_TUA_VPS`, Proxy `Ativado` (Nuvem Laranja).
        
    *   **Tipo CNAME**: Nome `www`, Conteúdo `@`, Proxy `Ativado`.
        
4.  **SSL/TLS**:
    
    *   Configurar modo de criptografia para **"Full"**.
        
    *   Ativar **"Always Use HTTPS"** em Edge Certificates.
        

### 5\. Configuração do Ambiente (.env na VPS)

Cria o ficheiro `.env` na raiz da VPS com estes valores ajustados para a Cloudflare:

Snippet de código

    # --- SEGURANÇA ---
    DEBUG=False
    DJANGO_SECRET_KEY=tua-chave-ultra-secreta
    WEBHOOK_HMAC_SECRET=teu-segredo-hmac
    
    # --- REDE E DOMÍNIO ---
    # Importante: usar HTTPS aqui pois a Cloudflare gerencia o SSL
    WHATSAPP_HOOK_URL=https://teu-dominio.com/webhook
    DJANGO_ALLOWED_HOSTS=teu-dominio.com,www.teu-dominio.com,IP_DA_VPS
    
    # --- FIX PARA CLOUDFLARE/NGINX ---
    # Sem isto o Django bloqueia o acesso ao Painel (CSRF Failure)
    CSRF_TRUSTED_ORIGINS=https://teu-dominio.com,https://www.teu-dominio.com
    SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https 

### 6\. Subida e Verificação (O Ritual)

*   \[ \] **Upload**: Subir o projeto (via Git ou SFTP) e o ficheiro `google-credentials.json` (manualmente).
    
*   \[ \] **Build**: `docker-compose build`
    
*   \[ \] **Up**: `docker-compose up -d`
    
*   \[ \] **Logs**: `docker logs -f django-docker`
    
    *   _Verificar:_ Se o `setup_waha` diz "Sessão configurada com sucesso".
        
*   \[ \] **Acesso**: Testar o acesso em `https://teu-dominio.com/portal-secreto-dev-m/`.
    

### 7\. Manutenção Periódica

*   \[ \] **Logs**: Verificar periodicamente o volume de logs para não encher o disco.
    
*   \[ \] **Cleanup**: Executar `docker image prune -f` após atualizações para limpar versões antigas da imagem.
    

***

### Benefícios desta Configuração:

*   **Custo Zero de SSL**: A Cloudflare trata de tudo sem renovações manuais.
    
*   **Performance**: Estáticos servidos pelo Nginx e cacheados globalmente pela Cloudflare.
    
*   **Resiliência**: O Django só aceita tráfego após o banco de dados e o motor do WhatsApp estarem prontos.


3. Dica de Segurança para Produção
Quando você tirar o site do seu computador (127.0.0.1) e colocar na internet (ex: quiro-admin.com.br):

Você terá que voltar no Google Cloud e atualizar as URLs para a nova versão com HTTPS.

O Google exige HTTPS para qualquer domínio que não seja o localhost ou 127.0.0.1.

Seu código já está no GitHub. Quando você contratar a Hostinger e logar no terminal dela, você só precisará de 4 comandos para ver esse arquivo virar realidade:

Bash

    # 1. Clonar seu projeto
    git clone https://github.com/SEU_USUARIO/chatbot_fastapi_edition.git
    cd chatbot_fastapi_edition
    
    # 2. Criar o arquivo .env (Você vai colar as senhas geradas lá)
    nano .env
    
    # 3. Criar o arquivo de credenciais do Google
    nano google-credentials.json
    
    # 4. SUBIR TUDO!
    docker-compose up -d --build