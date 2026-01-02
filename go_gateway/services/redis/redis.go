package redis

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/go-redis/redis/v8"
)

// ChannelName agora é o nome da nossa LISTA/FILA
const ChannelName = "new_user_queue"

// Client (será inicializado uma vez)
var Client *redis.Client

// IdempotencyClient (DB 3 - Chaves de Idempotência)
var IdempotencyClient *redis.Client

// --- NOVAS CONSTANTES PARA IDEMPOTÊNCIA ---
const idempotencyKeyPrefix = "idempotency:event:" // O prefixo para as chaves no Redis (boa prática)
const idempotencyTTL = time.Hour * 3             // TTL (Tempo de Vida) da chave: 24 horas (otimizado)

// --- NOVAS CONSTANTES PARA BLACKLIST ---
const blacklistKey = "system:blacklist:chat_ids" // Chave SET para a Blacklist

// Estrutura Mínima para extrair o ID do Payload WAHA.
type EventPayload struct {
	ID string `json:"id"`
}

// Estrutura Mínima para extrair o ChatID do Payload WAHA.
// CORREÇÃO: A tag JSON foi ajustada para "from" conforme a estrutura do seu payload.
type ChatPayload struct {
	ChatID string `json:"from"` 
}

// InitClient configura e testa a conexão com o Redis (DB 0)
func InitClient(ctx context.Context) error {
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "redis" // Default Docker Compose
	}

	Client = redis.NewClient(&redis.Options{
		Addr: fmt.Sprintf("%s:%s", redisHost, "6379"),
		DB: 0,
	})

	// Teste de conexão: PING com um timeout seguro de 3 segundos
	pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	
	_, err := Client.Ping(pingCtx).Result()
	if err != nil {
		return fmt.Errorf("falha ao conectar e pingar o Redis: %w", err)
	}
	
	return nil
}

// PublishMessage publica o payload bruto na fila (RPUSH)
func PublishMessage(ctx context.Context, rawBody []byte) error {
	publishCtx, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
	defer cancel()
	
	// ➡️ RPUSH (Right Push) para garantir FIFO. Apenas enfileira o payload BRUTO.
	if err := Client.RPush(publishCtx, ChannelName, rawBody).Err(); err != nil {
		return fmt.Errorf("falha ao publicar mensagem no Redis: %w", err)
	}
	
	return nil
}

func InitIdempotencyClient(ctx context.Context) error {
	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "redis"
	}

	IdempotencyClient = redis.NewClient(&redis.Options{
		Addr: fmt.Sprintf("%s:%s", redisHost, "6379"),
		DB: 3, // ⬅️ DB 3 para a idempotência
	})

	// Teste de conexão: PING com um timeout seguro de 3 segundos
	pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	
	_, err := IdempotencyClient.Ping(pingCtx).Result()
	if err != nil {
		return fmt.Errorf("falha ao conectar e pingar o Redis (DB 3): %w", err)
	}
	
	return nil
}

// CheckAndSetIdempotency: Tenta registrar o eventID no Redis.
func CheckAndSetIdempotency(ctx context.Context, eventID string) (bool, error) {
	key := idempotencyKeyPrefix + eventID 

	// SETNX (Set if Not Exists) é a operação atômica no IdempotencyClient (DB 3)
	result, err := IdempotencyClient.SetNX(ctx, key, "1", idempotencyTTL).Result()

	if err != nil {
		return false, fmt.Errorf("erro de comunicação com Redis DB 3 durante SETNX: %w", err)
	}

	// Se result == false, significa que a chave JÁ EXISTIA (Duplicata).
	return !result, nil
}

// =======================================================
// === FUNÇÕES DE EXTRAÇÃO CORRIGIDAS PARA NESTING ===
// =======================================================

// ExtractEventID: Analisa o JSON bruto para obter o ID único (payload.id).
func ExtractEventID(rawBody []byte) (string, error) {
	// 1. Estrutura temporária para extrair o objeto "payload" aninhado.
	var root struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(rawBody, &root); err != nil {
		return "", fmt.Errorf("falha ao desserializar payload raiz para encontrar o objeto 'payload': %w", err)
	}

	// 2. Desserializa o objeto aninhado "payload" na struct EventPayload original.
	var eventPayload EventPayload 
	if err := json.Unmarshal(root.Payload, &eventPayload); err != nil {
		return "", fmt.Errorf("falha ao desserializar ID do payload aninhado: %w", err)
	}
	
	if eventPayload.ID == "" {
		return "", errors.New("campo 'id' único (payload.id) não encontrado ou vazio no payload")
	}

	return eventPayload.ID, nil
}


// ExtractChatID: Analisa o JSON bruto para obter o Chat ID (payload.from).
func ExtractChatID(rawBody []byte) (string, error) {
	// 1. Estrutura temporária para extrair o objeto "payload" aninhado.
	var root struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(rawBody, &root); err != nil {
		return "", fmt.Errorf("falha ao desserializar payload raiz para encontrar o objeto 'payload': %w", err)
	}

	// 2. Desserializa o objeto aninhado "payload" na struct ChatPayload original.
	var chatPayload ChatPayload
	if err := json.Unmarshal(root.Payload, &chatPayload); err != nil {
		return "", fmt.Errorf("falha ao desserializar ChatID do payload aninhado: %w", err)
	}
	
	// A tag json:"from" (corrigida) é buscada dentro do objeto "payload".
	if chatPayload.ChatID == "" {
		return "", errors.New("campo 'from' (payload.from) não encontrado ou vazio no payload")
	}

	return chatPayload.ChatID, nil
}

// CheckBlacklist: Verifica se o ChatID está na lista negra (cheque O(1)).
// Usa o Client (DB 0).
func CheckBlacklist(ctx context.Context, chatID string) (bool, error) {
	result, err := Client.SIsMember(ctx, blacklistKey, chatID).Result()

	if err != nil {
		return false, fmt.Errorf("erro de comunicação com Redis DB 0 durante SIsMember: %w", err)
	}

	// result é 'true' se o membro existir no SET (está na blacklist)
	return result, nil
}