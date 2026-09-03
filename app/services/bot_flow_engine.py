"""
Bot Flow Engine - Interpretador e Executor do Fluxo Visual de Atendimento (Typebot)
Q-Aura Omnichannel Platform
"""

import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple


def normalize_text(text: Optional[str]) -> str:
    """
    Remove acentos, pontuação extra e converte para minúsculas para matching resiliente.
    Ex: 'Recepção!' -> 'recepcao'
    """
    if not text:
        return ""
    text = text.strip().lower()
    # Normaliza decompondo caracteres acentuados
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_text = nfkd.encode('ASCII', 'ignore').decode('utf-8')
    return ascii_text.strip()


class BotFlowEngine:
    def __init__(self, flow_data: Dict[str, Any], bot_config: Optional[Any] = None):
        self.flow_data = flow_data or {}
        self.nodes = {str(n.get("id")): n for n in self.flow_data.get("nodes", [])}
        self.connections = self.flow_data.get("connections", [])
        self.bot_config = bot_config

    def get_outgoing_nodes(self, node_id: str) -> List[Dict[str, Any]]:
        """Retorna lista de nós de destino conectados a partir do node_id."""
        targets = []
        for conn in self.connections:
            if str(conn.get("from")) == str(node_id):
                to_id = str(conn.get("to"))
                if to_id in self.nodes:
                    targets.append(self.nodes[to_id])
        return targets

    def find_start_node(self) -> Optional[Dict[str, Any]]:
        """
        Encontra o nó inicial do fluxo.
        1. Procura nó com type == 'start'
        2. Ou nó que não possui nenhuma conexão de entrada (root)
        3. Ou o primeiro nó cadastrado
        """
        if not self.nodes:
            return None

        # 1. Nó explícito 'start'
        for n in self.nodes.values():
            if n.get("type") == "start":
                return n

        # 2. Nós de destino conectados
        target_ids = {str(c.get("to")) for c in self.connections}
        for n_id, n in self.nodes.items():
            if n_id not in target_ids:
                return n

        # 3. Primeiro nó da lista
        first_id = list(self.nodes.keys())[0]
        return self.nodes[first_id]

    def _extract_condition_keywords(self, condition_node: Dict[str, Any]) -> List[str]:
        """
        Extrai palavras-chave ou termos a serem testados na condição.
        Formatos suportados:
        - "Se mensagem contém 'Recepção'" -> ['recepcao']
        - "Se mensagem contém 'reserva'" -> ['reserva']
        - "contém: 1, reserva" -> ['1', 'reserva']
        - "Falar com Atendente" -> ['falar com atendente', 'atendente']
        """
        content = condition_node.get("content", "") or ""
        title = condition_node.get("title", "") or ""

        keywords = []

        # Procura termos entre aspas simples ou duplas
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", content)
        for q in quoted:
            norm = normalize_text(q)
            if norm:
                keywords.append(norm)

        # Se não tiver aspas, tenta pegar após "contém", "igual", "e"
        if not keywords:
            clean = re.sub(r"(?i)^(se\s+mensagem\s+cont[eé]m|cont[eé]m|igual\s+a)\s*", "", content).strip()
            if clean:
                parts = [p.strip() for p in clean.split(",") if p.strip()]
                for p in parts:
                    norm = normalize_text(p)
                    if norm:
                        keywords.append(norm)

        # Se ainda vazio, usa o título se for descritivo
        if not keywords and title and normalize_text(title) != "regra condicional":
            norm_title = normalize_text(title)
            if norm_title:
                keywords.append(norm_title)

        return keywords

    def _extract_menu_options(self, menu_content: str) -> Dict[str, str]:
        """
        Analisa o texto de um menu com opções numeradas para mapear número -> texto da opção.
        Ex: '1. Reservas\\n2. Recepção\\n3. Falar com Atendente'
        -> {'1': 'reservas', '2': 'recepcao', '3': 'falar com atendente'}
        """
        mapping = {}
        if not menu_content:
            return mapping

        # Regex para linhas como "1. Reservas", "1 - Reservas", "1) Reservas"
        lines = menu_content.split("\n")
        for line in lines:
            line_clean = line.strip()
            match = re.match(r"^(\d+)[\.\-\)]\s*(.+)$", line_clean)
            if match:
                opt_num = match.group(1).strip()
                opt_text = normalize_text(match.group(2).strip())
                mapping[opt_num] = opt_text
        return mapping

    def matches_condition(
        self,
        condition_node: Dict[str, Any],
        user_message: str,
        parent_menu_content: Optional[str] = None
    ) -> bool:
        """
        Verifica se a mensagem do usuário satisfaz a condição.
        Suporta:
        - Digitação da palavra-chave (ex: 'reserva', 'recepção')
        - Digitação do número da opção (ex: '1', '2', '3') mapeado do menu pai
        """
        norm_user = normalize_text(user_message)
        if not norm_user:
            return False

        cond_keywords = self._extract_condition_keywords(condition_node)

        # 1. Checagem direta de palavra-chave
        for kw in cond_keywords:
            # Se o usuário digitou exatamente a palavra ou contém a palavra
            if kw in norm_user:
                return True
            # Se for um número exato
            if norm_user in [kw, f"{kw}.", f"#{kw}", f"opcao {kw}", f"opcao {kw}"]:
                return True

        # 2. Se o nó anterior era um menu de opções com números (1. Reservas, 2. Recepção, etc.)
        if parent_menu_content:
            menu_map = self._extract_menu_options(parent_menu_content)
            # Extrai apenas os dígitos digitados pelo usuário se houver (ex: '1', '2')
            user_digits = re.findall(r"\b\d+\b", norm_user)
            for digit in user_digits:
                if digit in menu_map:
                    option_label = menu_map[digit]
                    # Verifica se o texto da opção bate com a condição
                    for kw in cond_keywords:
                        if kw in option_label or option_label in kw:
                            return True

        return False

    def process_step(
        self,
        current_step_id: Optional[str],
        user_message: str
    ) -> Dict[str, Any]:
        """
        Executa uma etapa do fluxo.
        Retorna dicionário com:
        - replies: List[str] (mensagens a serem enviadas ao cliente)
        - next_step_id: Optional[str] (próximo nó onde o robô vai esperar resposta, ou None se finalizado)
        - action: 'continue' | 'waiting_input' | 'transfer' | 'end'
        - status: 'bot' | 'waiting' (fila humana)
        """
        replies: List[str] = []

        # Caso 1: Início da conversa (primeira mensagem do cliente)
        if not current_step_id or current_step_id == "start":
            start_node = self.find_start_node()
            if not start_node:
                return self._fallback_response()

            # Se o nó inicial for 'start', avança para os próximos conectados
            current_node = start_node
            if current_node.get("type") == "start":
                outgoing = self.get_outgoing_nodes(str(current_node.get("id")))
                if outgoing:
                    current_node = outgoing[0]
                else:
                    return self._fallback_response()

            # Percorre a cadeia inicial (ex: Mensagem de Boas-vindas -> Menu de Opções)
            return self._traverse_forward(current_node)

        # Caso 2: O cliente já estava em um nó esperando resposta (ex: no Menu de Opções ou Captura)
        if current_step_id not in self.nodes:
            # Nó salvo não existe mais no fluxo, reinicia pelo início
            return self.process_step(None, user_message)

        waiting_node = self.nodes[current_step_id]
        outgoing_nodes = self.get_outgoing_nodes(current_step_id)

        # Checa se há nós de condição saindo deste bloco
        condition_nodes = [n for n in outgoing_nodes if n.get("type") == "condition"]

        if condition_nodes:
            matched_target_node = None
            parent_menu_content = waiting_node.get("content", "")

            for cond_node in condition_nodes:
                if self.matches_condition(cond_node, user_message, parent_menu_content):
                    # Encontrou condição compatível! Pega o nó para onde a condição aponta
                    cond_outgoing = self.get_outgoing_nodes(str(cond_node.get("id")))
                    if cond_outgoing:
                        matched_target_node = cond_outgoing[0]
                    break

            if matched_target_node:
                # Condição satisfeita! Segue o fluxo a partir do nó de destino da condição
                return self._traverse_forward(matched_target_node)
            else:
                # Nenhuma condição satisfeita: Envia mensagem de opção não reconhecida
                fallback_text = (
                    "Não consegui identificar sua escolha. "
                    "Por favor, digite o número da opção desejada ou escreva com outras palavras."
                )
                # Mantém esperando no mesmo nó
                return {
                    "replies": [fallback_text],
                    "next_step_id": current_step_id,
                    "action": "waiting_input",
                    "status": "bot"
                }

        # Se o nó não tinha condições (ex: input simples que apenas conecta diretamente a outro nó)
        if outgoing_nodes:
            next_node = outgoing_nodes[0]
            return self._traverse_forward(next_node)

        # Se não tem saída, finaliza fluxo
        return {
            "replies": [],
            "next_step_id": None,
            "action": "end",
            "status": "bot"
        }

    def _traverse_forward(self, start_node: Dict[str, Any]) -> Dict[str, Any]:
        """
        Percorre os nós para frente a partir de `start_node`, coletando mensagens
        até encontrar um nó que aguarda interação do usuário (`buttons`, `input`)
        ou um nó terminal (`transfer`).
        """
        curr = start_node
        replies: List[str] = []
        visited = set()

        while curr:
            node_id = str(curr.get("id"))
            if node_id in visited:
                break
            visited.add(node_id)

            node_type = curr.get("type")
            node_content = (curr.get("content") or "").strip()

            if node_type == "text":
                if node_content:
                    replies.append(node_content)
                outgoing = self.get_outgoing_nodes(node_id)
                # Se conecta diretamente a outro nó (ex: Menu), continua navegando
                if outgoing and outgoing[0].get("type") != "condition":
                    curr = outgoing[0]
                    continue
                else:
                    # Se não conecta a mais nada ou conecta a condições, para aqui
                    return {
                        "replies": replies,
                        "next_step_id": node_id if outgoing else None,
                        "action": "waiting_input" if outgoing else "end",
                        "status": "bot"
                    }

            elif node_type == "buttons":
                # Bloco de Menu de Opções
                if node_content:
                    replies.append(node_content)
                # O menu aguarda resposta do usuário
                return {
                    "replies": replies,
                    "next_step_id": node_id,
                    "action": "waiting_input",
                    "status": "bot"
                }

            elif node_type == "input":
                # Bloco de Captura de Dado (Pergunta ao usuário)
                if node_content:
                    replies.append(node_content)
                return {
                    "replies": replies,
                    "next_step_id": node_id,
                    "action": "waiting_input",
                    "status": "bot"
                }

            elif node_type == "transfer":
                # Bloco de Transferência para Atendente Humano
                transfer_msg = node_content or "Certo! Estou transferindo seu atendimento para a nossa equipe. Um momento, por favor!"
                replies.append(transfer_msg)
                return {
                    "replies": replies,
                    "next_step_id": None,
                    "action": "transfer",
                    "status": "waiting"  # Fila humana
                }

            elif node_type == "condition":
                # Se caiu em uma condição diretamente, segue a saída dela
                outgoing = self.get_outgoing_nodes(node_id)
                if outgoing:
                    curr = outgoing[0]
                    continue
                break
            else:
                if node_content:
                    replies.append(node_content)
                break

        return {
            "replies": replies,
            "next_step_id": None,
            "action": "end",
            "status": "bot"
        }

    def _fallback_response(self) -> Dict[str, Any]:
        """Resposta de contingência caso não haja nós válidos."""
        welcome = "Olá! Seja bem-vindo ao nosso atendimento. Como posso ajudar você hoje?"
        if self.bot_config and getattr(self.bot_config, "welcome_message", None):
            welcome = self.bot_config.welcome_message
        return {
            "replies": [welcome],
            "next_step_id": None,
            "action": "waiting_input",
            "status": "bot"
        }
