import httpx
import json
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings

logger = logging.getLogger("hermes_service")

# Prompt base do Agente Hermes para atendimento simples (sem script personalizado)
DEFAULT_HERMES_BASE_INSTRUCTION = """Você é {agent_name}, assistente virtual oficial de atendimento via WhatsApp.
DIRETRIZES FUNDAMENTAIS:
1. Responda de forma acolhedora, educada, prestativa e natural em português.
2. Seja objetivo e proativo para ajudar o cliente a encontrar o que procura.
3. Baseie-se nas informações e regras da empresa abaixo para responder com precisão.

INFORMAÇÕES E REGRAS DA EMPRESA:
{company_context}
"""

# Instrução de transbordo precisa e estrita para não disparar transferências falsas
TRANSFER_INSTRUCTION = """

REGRA DE ATENDIMENTO HUMANO:
Apenas se o cliente solicitar explicitamente falar com um atendente humano, pessoa real, atendente, recepcionista ou gerente, adicione a tag [TRANSFERIR_HUMANO] no final da sua resposta. Caso contrário, responda à dúvida do cliente diretamente e NÃO adicione a tag [TRANSFERIR_HUMANO]."""


class HermesService:
    @staticmethod
    def build_system_prompt(agent_name: Optional[str], custom_prompt: Optional[str]) -> str:
        """
        Lógica de montagem do system prompt:
        - Se o usuário forneceu um script completo do agente, usa diretamente + instrução de transbordo.
        - Se não há prompt personalizado, usa o template padrão simples com o nome do agente.
        """
        cleaned = custom_prompt.strip() if custom_prompt else ""

        if cleaned:
            return cleaned + TRANSFER_INSTRUCTION
        else:
            name = agent_name.strip() if agent_name else "Assistente Virtual"
            return DEFAULT_HERMES_BASE_INSTRUCTION.format(
                agent_name=name,
                company_context="Atenda cordialmente os clientes e tire dúvidas sobre nossos serviços com clareza e brevidade."
            ) + TRANSFER_INSTRUCTION

    @staticmethod
    async def generate_response(
        bot_config: Any,
        incoming_text: str,
        history: List[Dict[str, str]],
        contact_name: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Envia a mensagem e o contexto otimizado para o Agente via HTTP.
        Retorna uma tupla (resposta_texto, deve_transferir_para_humano).
        """
        # 1. Obter endpoint e chave da API
        api_url = getattr(bot_config, "hermes_api_url", None) or getattr(settings, "HERMES_API_URL", None) or "https://agentesia-9router.4nvzqw.easypanel.host/v1/chat/completions"
        api_key = getattr(bot_config, "hermes_api_key", None) or getattr(settings, "HERMES_API_KEY", None) or "sk-10f2e981397deaea-xsefur-f0def252"
        model = getattr(bot_config, "hermes_model", None) or getattr(settings, "HERMES_MODEL", None) or "cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        max_tokens = getattr(bot_config, "hermes_max_tokens", None) or 1000
        temperature = getattr(bot_config, "hermes_temperature", None) or 0.7
        agent_name = getattr(bot_config, "hermes_agent_name", None) or "Assistente"
        system_prompt = getattr(bot_config, "hermes_system_prompt", None) or ""

        # 2. Monta o system prompt
        formatted_system = HermesService.build_system_prompt(agent_name, system_prompt)

        # 3. Histórico completo e detalhado da conversa (sem truncamento para atendimento 100% humanizado)
        trimmed_history = history[-80:] if len(history) > 80 else history

        messages_payload = [{"role": "system", "content": formatted_system}]
        for msg in trimmed_history:
            role = "assistant" if msg.get("role") in ["assistant", "bot"] else "user"
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            messages_payload.append({
                "role": role,
                "content": content
            })

        # 4. Adiciona a mensagem atual
        user_msg = f"{contact_name}: {incoming_text}" if (contact_name and contact_name != "Hóspede WhatsApp") else incoming_text
        messages_payload.append({"role": "user", "content": user_msg})

        # 5. Prepara payload
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
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post(api_url, headers=headers, json=request_body)
                if res.status_code == 200:
                    raw_text = res.text.strip()
                    # Parsing resiliente caso venha data: [DONE] no final
                    try:
                        data = res.json()
                    except Exception:
                        clean_body = re.sub(r'data:\s*\[DONE\].*$', '', raw_text, flags=re.DOTALL).strip()
                        data = json.loads(clean_body)

                    choices = data.get("choices", [])
                    if choices:
                        raw_reply = choices[0].get("message", {}).get("content", "").strip()
                    else:
                        raw_reply = data.get("response", "").strip()

                    # Verifica se o agente acionou explicitamente o transbordo
                    should_transfer = "[TRANSFERIR_HUMANO]" in raw_reply
                    clean_reply = raw_reply.replace("[TRANSFERIR_HUMANO]", "").strip()

                    if not clean_reply:
                        clean_reply = "Como posso te ajudar?"
                        should_transfer = False

                    return clean_reply, should_transfer
                else:
                    logger.error(f"[Hermes Service] Erro HTTP {res.status_code}: {res.text}")
                    return (
                        "Olá! Tive uma pequena oscilação momentânea, mas já estou aqui para te ajudar. Como posso te auxiliar?",
                        False
                    )
        except Exception as e:
            logger.error(f"[Hermes Service Exception]: {e}")
            return (
                "Olá! Tive uma pequena oscilação na resposta, mas já estou aqui para te ajudar. Pode repetir por gentileza?",
                False
            )

