# 🤖 WhatsApp Session Manager: Arquitetura Híbrida (Go + Python) & LLM Agents

> **Status de Produção:** 🚀 Deploy Ativo | **Stack:** Go, Python, Redis, PostgreSQL, Nginx, Docker.

Este projeto é um sistema de orquestração de mensagens para WhatsApp (via WAHA) que utiliza uma **Arquitetura Híbrida de Microserviços**. O objetivo é garantir alta concorrência na borda (Edge) e inteligência contextual profunda no processamento (Core).

A solução implementa **LLM Agents** com **Tool Calling** para realizar agendamentos, cadastros e cancelamentos em tempo real, integrados ao Google Calendar.

---

## 🏗️ Arquitetura de Engenharia

O sistema foi desenhado para resolver o problema de latência e concorrência em chatbots de alta demanda.

| Camada | Tecnologia | Função Técnica |
| :--- | :--- | :--- |
| **Edge Gateway** | **Go (Golang)** | Recebe Webhooks, valida HMAC (Segurança) e enfileira no Redis (LPUSH). Garante **latência < 10ms** na resposta ao provedor. |
| **Message Broker** | **Redis** | Atua como Buffer de Mensagens e gerenciador de Estado (Sessão do Usuário, Contexto e Cache). |
| **Core Workers** | **Python (Celery)** | Consome filas (BLPOP), gerencia a lógica de IA e executa Tool Calling. |
| **Reverse Proxy** | **Nginx** | Gerencia SSL, Rate Limiting e roteamento de tráfego entre os containers Docker. |
| **Persistência** | **PostgreSQL** | Armazenamento relacional de usuários, agendamentos e logs de auditoria. |

---

## 🧠 O "Cérebro": Orquestração de Agentes

O sistema utiliza uma estratégia de **Model Tiering** (Hierarquia de Modelos) para otimizar custo e latência, utilizando a Groq Cloud.

### Fluxo de Decisão:
1.  **Bot Detector (`Agent_bot_detector`)**:
    * **Modelo:** `gpt-oss-20b` (Leve/Rápido).
    * **Função:** Classificador binário. Detecta se a mensagem é SPAM ou outro Bot para *Early Exit* (economizando tokens).
2.  **Router (`Agent_router`)**:
    * **Modelo:** `gpt-oss-120b` (Robusto).
    * **Função:** Analisa a intenção complexa do usuário e encaminha para o especialista correto.
3.  **Especialistas (Specialists)**:
    * **Agent_date:** Gerencia verificação de slots e conflitos de agenda.
    * **Agent_register:** Valida dados e realiza cadastro (LGPD).
    * **Agent_cancel:** Consulta agendamentos e executa cancelamento via Tool.
    * **Agent_info:** RAG simples para informações institucionais.

---

## ⚙️ DevOps e Automação

O projeto segue práticas modernas de **Containerização e CI/CD**:

* **Entrypoint Inteligente (`entrypoint.sh`)**: O container Web verifica a saúde do Banco de Dados, roda migrações (`migrate`), coleta estáticos e configura o WAHA automaticamente no boot.
* **Self-Healing**: Containers configurados com `restart: unless-stopped` e Healthchecks nativos no Docker Compose.
* **Rotinas de Manutenção (`Celery Beat`)**:
    * 🕒 **Cleanup Service (03:00 AM):** Limpa slots de horários expirados no banco para manter a integridade das consultas.
    * 🔔 **Lembretes Automáticos:** Worker dedicado que verifica a agenda e envia lembretes proativos via WhatsApp.

---

## 🚀 Como Rodar (Setup de Produção)

### 1. Pré-requisitos
* Docker & Docker Compose instalados.
* Conta na Groq (API Key) e Google Cloud (Credentials).

### 2. Configuração
Clone o repositório e configure as variáveis de ambiente:

```bash
git clone [https://github.com/MaiconP-Y/chatbot_versionamento.git](https://github.com/MaiconP-Y/chatbot_versionamento.git)
cd chatbot_versionamento
cp .env.example .env
# Edite o .env com suas chaves