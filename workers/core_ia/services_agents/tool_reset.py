REROUTE_COMPLETED_STATUS = "REROUTE_COMPLETED"
RESET_SIGNAL = "__USER_WANTS_RESET__" 

def finalizar_user(history_str: str) -> str:
    """
    FUNÇÃO PURA: Apenas extrai a última mensagem do usuário e retorna um sinal
    para o Agent. REMOVE I/O e a chamada complexa ao roteador.
    """
    history_lines = history_str.split('\n')
    last_user_message_content = "qual o menu" 
    
    for line in reversed(history_lines):
        if line.startswith("[User]:"):
            last_user_message_content = line.replace("[User]:", "", 1).strip()
            break
            
    return f"{RESET_SIGNAL}|{last_user_message_content}"