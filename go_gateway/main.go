package main

import (
	"context"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	"go_waha_gateway/services/hmac"
	"go_waha_gateway/services/redis"
)

var ctx = context.Background()

const MAX_BODY_SIZE int64 = 1048576

func main() {
	// 1. Inicializa o Serviço HMAC
	if err := hmac.InitSecret(); err != nil {
		log.Fatalf("❌ Falha crítica ao carregar a chave HMAC: %v", err)
	}

	// 2. Inicializa o Serviço Redis (DB 0)
	if err := redis.InitClient(ctx); err != nil {
		log.Fatalf("❌ Falha crítica ao inicializar o Redis (DB 0): %v", err)
	}
	log.Println("✅ Conexão Redis DB 0 (Fila/Blacklist) estabelecida com sucesso!")

	// 2.1. Inicializa o Serviço Redis (DB 3)
	if err := redis.InitIdempotencyClient(ctx); err != nil {
		log.Fatalf("❌ Falha crítica ao inicializar o Redis (DB 3 - Idempotência): %v", err)
	}
	log.Println("✅ Conexão Redis DB 3 (Idempotência) estabelecida com sucesso!")

	// --- CONFIGURAÇÃO DO WORKER POOL (O "GO WAY" PARA ECONOMIA) ---
	// Criamos a fila interna (buffer de 50 é ideal para 12 agendamentos/dia)
	jobQueue := make(chan []byte, 50)

	// Iniciamos 3 workers fixos que substituirão a criação desenfreada de goroutines
	for i := 1; i <= 3; i++ {
		go worker(jobQueue)
	}

	// 3. Configuração do Servidor HTTP
	mux := http.NewServeMux()
	mux.HandleFunc("/webhook", func(w http.ResponseWriter, r *http.Request) {
		webhookHandler(w, r, jobQueue)
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	// Definindo timeouts para evitar consumo desnecessário de RAM em conexões presas
	server := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
		IdleTimeout:  15 * time.Second,
	}

	log.Printf("🚀 Gateway Go INICIADO na porta :%s (Worker Pool Ativa)", port)
	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("❌ Erro fatal ao iniciar o servidor: %v", err)
	}
}

func webhookHandler(w http.ResponseWriter, r *http.Request, jobQueue chan<- []byte) {
	// PASSO 1: Leitura do Body e limite
	r.Body = http.MaxBytesReader(w, r.Body, MAX_BODY_SIZE)
	rawBody, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("❌ Erro ao ler body do request ou limite excedido: %v", err)
		http.Error(w, "Bad Request: Invalid body or size limit exceeded", http.StatusBadRequest)
		return
	}

	// PASSO 2: Validação HMAC
	hmacHeader := r.Header.Get("X-Webhook-Hmac")
	if hmacHeader == "" || !hmac.ValidateHmac(rawBody, hmacHeader) {
		log.Println("❌ Requisição recusada: HMAC ausente ou inválido.")
		http.Error(w, "Forbidden: Invalid HMAC signature", http.StatusForbidden)
		return
	}

	// PASSO 3: Responde HTTP 200 OK IMEDIATAMENTE
	// No padrão de pool, enviamos para o canal. Se o canal estiver cheio, avisamos erro 503.
	select {
	case jobQueue <- rawBody:
		w.WriteHeader(http.StatusOK)
		log.Println("✨ Webhook aceito e enviado para fila interna.")
	default:
		log.Println("⚠️ Fila interna cheia. Descartando para proteger recursos.")
		http.Error(w, "Service Unavailable: Worker pool full", http.StatusServiceUnavailable)
	}
}

// O Worker executa exatamente o seu PASSO 4 original
func worker(jobs <-chan []byte) {
	for payload := range jobs {
		// Contexto de curta duração para o Redis (5s)
		ctxRedis, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		
		// 1. Variáveis de Controle
		var shouldPublish = true
		var eventID string

		// --- 4.1. BARREIRA 1.5: RIGIDEZ DO EVENT ID ---
		eventID, eventIDErr := redis.ExtractEventID(payload)
		if eventIDErr != nil || eventID == "" {
			log.Printf("⚠️ BARREIRA 1.5: ID de evento ausente/inválido. Erro: %v", eventIDErr)
			shouldPublish = false
		}

		// --- 4.2. BARREIRA 2: IDEMPOTÊNCIA ---
		if shouldPublish {
			isDuplicate, idempotencyErr := redis.CheckAndSetIdempotency(ctxRedis, eventID)
			if idempotencyErr != nil {
				log.Printf("❌ ERRO Redis CRÍTICO (Idempotência): %v", idempotencyErr)
			} else if isDuplicate {
				log.Printf("❌ BARREIRA 2: DUPLICATA DESCARTADA. ID: %s", eventID)
				shouldPublish = false
			}
		}

		// --- 4.3. BARREIRA 3: BLACKLIST & VALIDAÇÃO DE CHATID ---
		if shouldPublish {
			chatID, chatIDErr := redis.ExtractChatID(payload)
			if chatIDErr == nil && chatID != "" {
				isBlacklisted, blacklistErr := redis.CheckBlacklist(ctxRedis, chatID)
				if blacklistErr != nil {
					log.Printf("❌ ERRO Redis CRÍTICO (Blacklist): %v", blacklistErr)
				} else if isBlacklisted {
					log.Printf("🚫 BARREIRA 3: BLACKLIST Chat ID %s DESCARTADO.", chatID)
					shouldPublish = false
				} else {
					log.Printf("✅ Evento ÚNICO aceito. ID: %s | Chat ID: %s", eventID, chatID)
				}
			} else {
				log.Printf("⚠️ Chat ID ausente/inválido. Publicando sem Blacklist. ID: %s", eventID)
			}
		}

		// --- 4.4: Publicação na Fila ---
		if shouldPublish {
			if pubErr := redis.PublishMessage(ctxRedis, payload); pubErr != nil {
				log.Printf("❌ ERRO ASSÍNCRONO CRÍTICO: Falha ao publicar no Redis: %v", pubErr)
			} else {
				log.Println("✅ Payload ENFILEIRADO com sucesso.")
			}
		}
		
		cancel() // Libera o contexto após processar a mensagem
	}
}