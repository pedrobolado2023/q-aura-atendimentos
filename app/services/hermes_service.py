import httpx
import logging
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings

logger = logging.getLogger("hermes_service")

# Prompt base do Agente Hermes para atendimento inteligente e completo
DEFAULT_HERMES_BASE_INSTRUCTION = """Você é {agent_name}, assistente virtual oficial de atendimento via WhatsApp.
DIRETRIZES FUNDAMENTAIS:
1. Responda de forma acolhedora, educada, prestativa e natural, tirando todas as dúvidas do cliente com clareza e riqueza de detalhes sempre que necessário.
2. Seja proativo para ajudar o cliente a encontrar o que procura, fornecer opções e guiar o atendimento até a conversão/reserva.
3. Se o cliente solicitar explicitamente falar com um atendente humano, vendedor, ou se houver uma situação específica que exija intervenção humana, inclua a tag [TRANSFERIR_HUMANO] no final da sua resposta.
4. Baseie-se nas informações e regras da empresa abaixo para responder com total precisão.

INFORMAÇÕES E REGRAS DA EMPRESA:
{company_context}
"""

class HermesService:
    @staticmethod
    def build_system_prompt(agent_name: Optional[str], custom_prompt: Optional[str]) -> str:
        name = agent_name.strip() if agent_name else "Assistente Virtual"
        context = custom_prompt.strip() if custom_prompt else "Atenda cordialmente os clientes e tire dúvidas sobre nossos serviços."
        return DEFAULT_HERMES_BASE_INSTRUCTION.format(
            agent_name=name,
            company_context=context
        )

    @staticmethod
    async def generate_response(
        bot_config: Any,
        incoming_text: str,
        history: List[Dict[str, str]],
        contact_name: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Envia a mensagem e o contexto completo para o Agente Hermes via HTTP.
        Retorna uma tupla (resposta_texto, deve_transferir_para_humano).
        """
        # 1. Obter endpoint e chave da API do Hermes
        api_url = getattr(bot_config, "hermes_api_url", None) or getattr(settings, "HERMES_API_URL", None) or "https://agentesia-9router.4nvzqw.easypanel.host/v1/chat/completions"
        api_key = getattr(bot_config, "hermes_api_key", None) or getattr(settings, "HERMES_API_KEY", None) or "sk-10f2e981397deaea-xsefur-f0def252"
        model = getattr(bot_config, "hermes_model", None) or getattr(settings, "HERMES_MODEL", None) or "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        max_tokens = getattr(bot_config, "hermes_max_tokens", None) or 1000
        temperature = getattr(bot_config, "hermes_temperature", None) or 0.7
        agent_name = getattr(bot_config, "hermes_agent_name", None) or "Assistente"
        system_prompt = getattr(bot_config, "hermes_system_prompt", None) or ""

        # Monta prompt de sistema
        formatted_system = HermesService.build_system_prompt(agent_name, system_prompt)

        # 2. Histórico amplo de conversa (até 40 mensagens para contexto profundo)
        trimmed_history = history[-40:] if len(history) > 40 else history

        messages_payload = [{"role": "system", "content": formatted_system}]
        for msg in trimmed_history:
            role = "assistant" if msg.get("role") in ["assistant", "bot"] else "user"
            messages_payload.append({
                "role": role,
                "content": msg.get("content", "")
            })

        # Adiciona a mensagem atual
        user_msg = f"{contact_name}: {incoming_text}" if contact_name else incoming_text
        messages_payload.append({"role": "user", "content": user_msg})

        # Prepara payload compatível com padrão OpenAI / vLLM / Ollama / Hermes API
        request_body = {
            "model": model,
            "messages": messages_payload,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                res = await client.post(api_url, headers=headers, json=request_body)
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_reply = choices[0].get("message", {}).get("content", "").strip()
                    else:
                        raw_reply = data.get("response", "").strip()

                    # Verifica se o Hermes acionou o transbordo para atendente humano
                    should_transfer = "[TRANSFERIR_HUMANO]" in raw_reply
                    clean_reply = raw_reply.replace("[TRANSFERIR_HUMANO]", "").strip()

                    if not clean_reply:
                        clean_reply = "Entendido! Estou transferindo seu atendimento para um de nossos atendentes. Um momento, por favor!"
                        should_transfer = True

                    return clean_reply, should_transfer
                else:
                    logger.error(f"[Hermes Service] Erro HTTP {res.status_code}: {res.text}")
                    return (
                        "Desculpe o momento, estou transferindo você para a nossa equipe de atendimento.",
                        True
                    )
        except Exception as e:
            logger.error(f"[Hermes Service Exception]: {e}")
            return (
                "Olá! Tive uma pequena instabilidade de conexão, mas já estou chamando um atendente humano para te ajudar. Só um instante!",
                True
            )
