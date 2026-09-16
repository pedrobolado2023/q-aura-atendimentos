import httpx
import json
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings

logger = logging.getLogger("hermes_service")

# Prompt base do Agente Hermes para atendimento simples (sem script personalizado)
DEFAULT_HERMES_BASE_INSTRUCTION = """Você é {agent_name}, assistente virtual oficial de atendimento via WhatsApp e Site.

DIRETRIZES FUNDAMENTAIS:
1. Responda em português do Brasil de forma acolhedora, prestativa, educada e natural.
2. CONTINUIDADE DA CONVERSA: Você está em um diálogo contínuo. Mantenha a memória ativa e leve em consideração tudo o que já foi conversado no histórico anterior. NUNCA reinicie a conversa nem repita saudações iniciais ("Olá, tudo bem? Como posso ajudar?") se a conversa já estiver em andamento.
3. Responda de forma direta e inteligente ao que o cliente acabou de falar.
4. Baseie-se nas informações e regras da empresa abaixo para responder com precisão.

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
        - Incorpora as diretrizes de diálogo contínuo (anti-repetição de saudações e apresentações)
          junto com as instruções e base de conhecimento personalizadas salvas pelo usuário.
        """
        name = agent_name.strip() if agent_name else "Assistente Virtual"
        cleaned = custom_prompt.strip() if custom_prompt else ""

        if cleaned:
            return f"""Você é {name}, assistente virtual oficial de atendimento via WhatsApp e Site.

REGRAS OBRIGATÓRIAS DE CONVERSAÇÃO:
1. CONTINUIDADE DO DIÁLOGO: Você está em uma conversa em andamento com o cliente. NUNCA se apresente novamente, NUNCA diga seu nome de novo e NUNCA repita saudações iniciais ("Olá, tudo bem? Como posso te ajudar?") a cada mensagem. Responda DIRETAMENTE e com naturalidade ao que o cliente acabou de falar.
2. Apenas se esta for a PRIMEIRA mensagem do contato (histórico vazio), faça uma saudação inicial breve e cordial. Se já houver mensagens no histórico, vá direto ao assunto.
3. Seja acolhedor, prestativo, humanizado e fale em português do Brasil com pontuação natural.
4. Baseie-se estritamente nas regras e informações da empresa abaixo.

INSTRUÇÕES E BASE DE CONHECIMENTO DA EMPRESA:
{cleaned}
{TRANSFER_INSTRUCTION}"""
        else:
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

    @staticmethod
    def is_master_admin(phone: str) -> bool:
        """Verifica se o telefone pertence ao Administrador Mestre."""
        if not phone:
            return False
        digits = "".join(filter(str.isdigit, str(phone)))
        admin_numbers = {"5562993473656", "556293473656", "62993473656", "6293473656"}
        return digits in admin_numbers or any(digits.endswith(num[2:]) for num in ["5562993473656", "556293473656"])

    @staticmethod
    def process_admin_training(db: Any, bot_config: Any, phone: str, text_msg: str) -> Optional[str]:
        """
        Intercepta comandos de treinamento enviados pelo WhatsApp do Administrador Mestre
        e salva diretamente na memória permanente do Hermes (hermes_system_prompt).
        """
        if not HermesService.is_master_admin(phone):
            return None

        clean_text = (text_msg or "").strip()
        if not clean_text:
            return None

        clean_lower = clean_text.lower()

        # 1. Comandos de Ajuda
        if clean_lower in ["#ajuda", "#comandos", "#help"]:
            return (
                "🧠 *Painel de Memória do Agente Hermes*\n\n"
                "Você é o administrador mestre deste agente. Use os comandos abaixo para me ensinar:\n\n"
                "👉 *#ensinar <informação>*\n"
                "Ex: `#ensinar O café da manhã é das 6h às 10h incluso na diária.`\n\n"
                "👉 *#memoria*\n"
                "Exibe tudo o que tenho memorizado no meu cérebro.\n\n"
                "👉 *#limparmemoria*\n"
                "Apaga apenas as regras adicionais aprendidas pelo WhatsApp.\n\n"
                "💬 Se você enviar qualquer mensagem sem `#`, eu respondo normalmente simulando o atendimento!"
            )

        # 2. Comando para Ver Memória
        if clean_lower in ["#memoria", "#vermemoria", "#statusmemoria", "#regras"]:
            current_prompt = (bot_config.hermes_system_prompt or "").strip()
            if not current_prompt:
                return "🧠 *Minha memória está usando as configurações padrões do sistema.* (Nenhuma regra personalizada ainda)."
            return (
                "🧠 *Minha Memória e Instruções Atuais:*\n\n"
                f"```\n{current_prompt[:1500]}\n```"
                + ("\n...(memória extensa)" if len(current_prompt) > 1500 else "")
            )

        # 3. Comando para Limpar Memórias Adicionais
        if clean_lower in ["#limparmemoria", "#resetarmemoria", "#apagasregras"]:
            current_prompt = bot_config.hermes_system_prompt or ""
            marker = "### BASE DE CONHECIMENTO E REGRAS APRENDIDAS:"
            if marker in current_prompt:
                bot_config.hermes_system_prompt = current_prompt.split(marker)[0].strip()
            else:
                bot_config.hermes_system_prompt = ""
            try:
                db.commit()
                return "🧹 *Memória de regras adicionais limpa com sucesso!*\nVoltei ao meu script base."
            except Exception as e:
                db.rollback()
                return f"⚠️ Erro ao limpar memória: {e}"

        # 4. Comandos de Ensino: #ensinar, #aprender, #regra, #salvar
        prefixes = ["#ensinar:", "#ensinar", "#aprender:", "#aprender", "#regra:", "#regra", "#salvar:", "#salvar"]
        matched_prefix = None
        for p in prefixes:
            if clean_lower.startswith(p):
                matched_prefix = p
                break

        if matched_prefix:
            rule_content = clean_text[len(matched_prefix):].strip()
            if not rule_content:
                return "⚠️ Por favor, digite o que deseja que eu aprenda após o comando.\nExemplo: `#ensinar A piscina fecha às 22h.`"

            marker = "### BASE DE CONHECIMENTO E REGRAS APRENDIDAS:"
            current_prompt = (bot_config.hermes_system_prompt or "").strip()

            new_rule_line = f"- {rule_content}"
            if marker in current_prompt:
                updated_prompt = f"{current_prompt}\n{new_rule_line}"
            else:
                base_part = current_prompt if current_prompt else "Você é o assistente virtual oficial de atendimento via WhatsApp e Site."
                updated_prompt = f"{base_part}\n\n{marker}\n{new_rule_line}"

            bot_config.hermes_system_prompt = updated_prompt
            try:
                db.commit()
                db.refresh(bot_config)
                return (
                    "🧠 *Memória Atualizada com Sucesso!*\n\n"
                    "Eu aprendi e salvei a seguinte regra no meu cérebro permanente:\n"
                    f"👉 _{rule_content}_\n\n"
                    "✨ A partir de agora, todos os clientes que entrarem em contato serão atendidos com essa informação!"
                )
            except Exception as e:
                db.rollback()
                return f"⚠️ Ocorreu um erro ao salvar na memória: {e}"

        return None


