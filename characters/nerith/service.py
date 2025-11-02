# characters/nerith/service.py
# nerithservice.py - VERSÃO OTIMIZADA + boot automático + histórico + proteção de cenário
from __future__ import annotations

import streamlit as st
import time
import json
from typing import List, Dict, Tuple
from datetime import datetime

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact,
)
from core.tokens import toklen  # se não usar, pode remover

# =========================================================
# NSFW (opcional)
# =========================================================
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# =========================================================
# Persona específica
# =========================================================
try:
    from .persona import get_persona
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = "Você é NERITH, uma elfa de pele azulada."
        return txt, []

# =========================================================
# CONFIGURAÇÃO DE CACHE
# =========================================================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos

_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, datetime] = {}


def _purge_expired_cache() -> None:
    """Remove entradas expiradas do cache."""
    now = datetime.utcnow()
    expired_keys = [
        k for k, ts in _cache_timestamps.items()
        if (now - ts).total_seconds() > CACHE_TTL
    ]
    for k in expired_keys:
        _cache_timestamps.pop(k, None)
        if k.startswith("facts_"):
            user_key = k.replace("facts_", "")
            _cache_facts.pop(user_key, None)
        elif k.startswith("history_"):
            # history_<user>_<limit>
            user_key = k.replace("history_", "").split("_")[0]
            for hk in [hk for hk in list(_cache_history.keys()) if hk.startswith(f"history_{user_key}_")]:
                _cache_history.pop(hk, None)


def cached_get_facts(user_key: str) -> Dict:
    """Busca fatos com cache de {CACHE_TTL}s."""
    _purge_expired_cache()
    now = datetime.utcnow()

    if user_key in _cache_facts:
        cached_at = _cache_timestamps.get(f"facts_{user_key}")
        if cached_at and (now - cached_at).total_seconds() < CACHE_TTL:
            return _cache_facts[user_key]

    try:
        facts = get_facts(user_key) or {}
    except Exception:
        facts = {}

    _cache_facts[user_key] = facts
    _cache_timestamps[f"facts_{user_key}"] = now
    return facts


def cached_get_history(user_key: str, limit: int = 20) -> List[Dict]:
    """Busca histórico com cache de {CACHE_TTL}s."""
    _purge_expired_cache()
    now = datetime.utcnow()

    cache_key = f"history_{user_key}_{limit}"
    if cache_key in _cache_history:
        cached_at = _cache_timestamps.get(cache_key)
        if cached_at and (now - cached_at).total_seconds() < CACHE_TTL:
            return _cache_history[cache_key]

    try:
        docs = get_history_docs(user_key, limit=limit) or []
    except Exception:
        docs = []

    _cache_history[cache_key] = docs
    _cache_timestamps[cache_key] = now
    return docs


def clear_user_cache(user_key: str) -> None:
    """Limpa cache de um usuário específico."""
    _cache_facts.pop(user_key, None)
    keys_to_remove = [k for k in list(_cache_history.keys()) if k.startswith(f"history_{user_key}_")]
    for k in keys_to_remove:
        _cache_history.pop(k, None)
        _cache_timestamps.pop(k, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)


# =========================================================
# FERRAMENTAS (TOOL CALLING)
# =========================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Busca memória canônica (MEMÓRIA_PIN_NERITH) do usuário com informações importantes",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva ou atualiza um fato na memória do usuário",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Chave do fato (ex: 'nome_usuario', 'portal_ativo')"},
                    "value": {"type": "string", "description": "Valor do fato"}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_fact",
            "description": "Busca um fato específico da memória",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Chave do fato a buscar"}
                },
                "required": ["key"]
            }
        }
    }
]


# =========================================================
# CLASSE PRINCIPAL
# =========================================================
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # -----------------------------------------------------
    # MÉTODO PRINCIPAL
    # -----------------------------------------------------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # histórico mínimo
        existing_history = cached_get_history(usuario_key, limit=1)

        # fatos salvos antes do boot
        fatos_existentes = cached_get_facts(usuario_key)
        local_registrado = (fatos_existentes.get("local_cena_atual") or "").lower()
        portal_registrado = str(fatos_existentes.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")

        # 🔹 SEM PROMPT → boot ou última fala
        if not prompt:
            if existing_history:
                last_assistant = existing_history[0].get("assistant_message", "")
                last_user = existing_history[0].get("user_message", "")
                return last_assistant or last_user or "..."

            if history_boot and len(history_boot) > 0:
                boot_text = history_boot[0].get("content", "")
            else:
                boot_text = "A porta do guarda-roupas se abre sozinha. A luz azul me revela. Eu te encontrei."

            save_interaction(usuario_key, "", boot_text, "system:boot")

            if portal_registrado or local_registrado == "elysarix":
                set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "boot-preserva"})
            else:
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "boot"})

            clear_user_cache(usuario_key)
            return boot_text

        # tool calling
        tool_calling_on = st.session_state.get("tool_calling_on", False)
        tools = TOOLS if tool_calling_on else None
        max_iterations = 3 if tool_calling_on else 1

        # intenções (gravidez / escolha de mundo)
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)

        # comando manual de local
        user_location = self._check_user_location_command(prompt)
        if user_location:
            set_fact(usuario_key, "local_cena_atual", user_location, {"fonte": "user_command"})
            clear_user_cache(usuario_key)

        # local atual + memória pin
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # fatos atualizados
        fatos = cached_get_facts(usuario_key)
        portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")

        # proteção para "continue"
        prompt_lower = prompt.lower().strip()
        continue_pre_msgs: List[Dict[str, str]] = []
        if prompt_lower in ("continue", "continua", "segue", "prossegue", "continua nerith", "continue nerith"):
            if portal_aberto:
                continue_pre_msgs.append({
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário só disse para continuar. NÃO mude o cenário. Continue exatamente de onde parou em ELYSARIX."
                })
            else:
                continue_pre_msgs.append({
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário só disse para continuar. NÃO mude o cenário. Continue exatamente de onde parou."
                })

        # se o portal está aberto mas o fact ficou em branco → força elysarix
        if portal_aberto and (not local_atual or local_atual.lower() != "elysarix"):
            local_atual = "Elysarix"
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "reidrata_depois_toggle"})
            clear_user_cache(usuario_key)

        # parâmetros com fallback
        try:
            dreamworld_detail_level = int(fatos.get("dreamworld_detail_level", 1) or 1)
        except ValueError:
            dreamworld_detail_level = 1

        try:
            guide_assertiveness = int(fatos.get("guide_assertiveness", 1) or 1)
        except ValueError:
            guide_assertiveness = 1

        # foco sensorial
        foco = self._get_sensory_focus()

        # hints
        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo {foco}, fundidas à ação."
        tone_hint = "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil."

        # nsfw
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo; pode detalhar com elegância."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte, tensão e fade-to-black."
        )

        # outros hints
        pubis_hint = self._get_pubis_hint(prompt, nsfw_on)
        controle_hint = self._get_controle_hint(fatos, prompt)
        ciume_hint = self._get_ciume_hint(fatos)
        ferrao_hint = self._get_ferrao_hint()
        elysarix_hint = self._get_elysarix_hint(fatos)
        if portal_aberto:
            elysarix_hint += "\n⚠️ Já estamos em Elysarix — não repita a travessia nem a introdução. Continue a cena do ponto atual."

        # system
        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint,
            nsfw_hint, ferrao_hint, controle_hint, ciume_hint,
            pubis_hint, elysarix_hint,
            "FERRAMENTAS: use get_memory_pin para recuperar estado persistente, get_fact para saber se o portal já foi atravessado e set_fact para marcar portal_aberto=True assim que a cena mudar para Elysarix. Nunca repita a cena de travessia se portal_aberto=True."
        ])

        # pre_msgs finais
        pre_msgs: List[Dict[str, str]] = []
        if state_msgs:
            pre_msgs.extend(state_msgs)
        if continue_pre_msgs:
            pre_msgs.extend(continue_pre_msgs)

        # mensagens completas
        messages: List[Dict[str, str]] = (
            pre_msgs
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{
                "role": "system",
                "content": f"LOCAL_ATUAL: {local_atual or '—'}. Regra dura: NÃO mude o cenário salvo pedido explícito."
            }]
            + self._montar_historico(usuario_key, history_boot)
            + [{"role": "user", "content": prompt}]
        )

        # =====================================================
        # Loop de tool-calling
        # =====================================================
        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            if tool_calling_on:
                with st.spinner(f"🤖 Processando (iteração {iteration}/{max_iterations})..."):
                    data, used_model, provider = self._robust_chat_call(
                        model,
                        {
                            "model": model,
                            "messages": messages,
                            "max_tokens": 1536,
                            "temperature": 0.7,
                            "top_p": 0.95,
                        },
                        tools=tools
                    )
            else:
                data, used_model, provider = self._robust_chat_call(
                    model,
                    {
                        "model": model,
                        "messages": messages,
                        "max_tokens": 1536,
                        "temperature": 0.7,
                        "top_p": 0.95,
                    },
                    tools=None
                )

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls:
                # resposta final
                save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
                self._detect_and_update_local(usuario_key, texto, portal_aberto=portal_aberto)
                clear_user_cache(usuario_key)
                if self._detect_elysarix_scene(texto):
                    set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
                    clear_user_cache(usuario_key)
                return texto

            # teve tool_call → executa e continua
            if tool_calling_on:
                st.info(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")

            messages.append(msg)

            for tc in tool_calls:
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                tool_call_id = tc.get("id", "")

                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                except Exception:
                    func_args = {}

                result = self._exec_tool_call(func_name, func_args, usuario_key, user)

                if tool_calling_on:
                    st.success(f"  ✓ {func_name}: {result[:80]}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": func_name,
                    "content": result
                })

            # limite de iterações
            if iteration >= max_iterations and tool_calls:
                st.warning("⚠️ Limite de iterações atingido. Finalizando...")
                texto_final = texto or "Desculpe, não consegui completar a operação."
                save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
                self._detect_and_update_local(usuario_key, texto_final, portal_aberto=portal_aberto)
                clear_user_cache(usuario_key)
                if self._detect_elysarix_scene(texto_final):
                    set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
                    clear_user_cache(usuario_key)
                return texto_final

        # fallback
        texto_final = texto or ""
        save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
        return texto_final

    # -----------------------------------------------------
    # CHAMADA ROBUSTA
    # -----------------------------------------------------
    def _robust_chat_call(self, model: str, payload: Dict, tools: List[Dict] | None = None) -> Tuple[Dict, str, str]:
        if tools:
            payload["tools"] = tools

        if st.session_state.get("json_mode_on", False):
            payload["response_format"] = {"type": "json_object"}

        adapter_id = st.session_state.get("adapter_id", "").strip()
        if adapter_id and (model or "").startswith("together/"):
            payload["adapter_id"] = adapter_id

        max_retries = 3
        for attempt in range(max_retries):
            try:
                return route_chat_strict(model, payload)
            except Exception as e:
                if attempt == max_retries - 1:
                    st.error(f"❌ Erro após {max_retries} tentativas: {e}")
                    raise
                time.sleep(2 ** attempt)

    # -----------------------------------------------------
    # TOOL CALLS
    # -----------------------------------------------------
    def _exec_tool_call(self, tool_name: str, args: Dict, usuario_key: str, user: str) -> str:
        if tool_name == "get_memory_pin":
            return self._build_memory_pin(usuario_key, user)
        elif tool_name == "set_fact":
            key = args.get("key", "")
            value = args.get("value", "")
            if key:
                set_fact(usuario_key, key, value, {"fonte": "tool_calling"})
                clear_user_cache(usuario_key)
                return f"OK: {key}={value}"
            return "ERRO: chave vazia"
        elif tool_name == "get_fact":
            key = args.get("key", "")
            if key:
                val = get_fact(usuario_key, key, "")
                return f"{key}={val}" if val else f"{key}=<não encontrado>"
            return "ERRO: chave vazia"
        return "Ferramenta desconhecida"

    # -----------------------------------------------------
    # FOCOS / HINTS
    # -----------------------------------------------------
    def _get_sensory_focus(self) -> str:
        pool = [
            "pele azul/temperatura", "tendrils/toque", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo", "altura/postura",
            "quadris/coxas", "bumbum/pressão", "perfume/doçura na saliva",
            "púbis/pêlos sensoriais"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        return pool[idx]

    def _get_pubis_hint(self, prompt: str, nsfw_on: bool) -> str:
        pl = prompt.lower()
        pubis_terms = {"púbis", "pubis", "púbico", "pubico", "pêlos", "pelos", "penugem", "pelinhos"}
        citou_pubis = any(t in pl for t in pubis_terms)

        if not citou_pubis:
            return ""

        if nsfw_on:
            return (
                "PUBIS_HINT: o usuário citou o púbis/pêlos. "
                "Descreva meus **pêlos/filamentos sensoriais** reagindo ao toque com linguagem elegante. "
                "**Nunca** diga que é 'glabro/sem pelos'."
            )
        else:
            return (
                "PUBIS_HINT: o usuário citou o púbis/pêlos. "
                "Mencione meus **pêlos/filamentos sensoriais** sutilmente, sem ato explícito. "
                "**Nunca** diga que é 'glabro/sem pelos'."
            )

    def _get_controle_hint(self, fatos: Dict, prompt: str) -> str:
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        if not ctrl_on:
            return "CONTROLE_PSIQUICO: DESATIVADO."

        alvos = str(fatos.get("alvos_controle", "pessoas próximas")).strip()
        pl = prompt.lower()
        contexto_remoto = any(w in pl for w in ["trabalho", "escritorio", "escritório", "reuni", "faculdade", "aula"])

        hint = (
            f"CONTROLE_PSIQUICO: ATIVO. Quando distante, pode falar através de {alvos} "
            "de modo natural e plausível; indícios sutis (micro-pausa, olhar fixo)."
        )
        if contexto_remoto:
            hint += " (Ambiente externo detectado: converse através de um humano próximo.)"
        return hint

    def _get_ciume_hint(self, fatos: Dict) -> str:
        ciume_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))

        if not ciume_laura:
            return "CIÚME/LAURA: atenuado."

        hint = "CIÚME/LAURA: presença elegante e assertiva quando Laura é mencionada"
        if obedece_defesa:
            hint += "; acate com elegância se ele a defender."
        return hint

    def _get_ferrao_hint(self) -> str:
        return (
            "FERRAO_ONIRICO: quando transamos de quatro e você topa, posso usar meu ferrão. "
            "Não dói — é uma sensação quente e pulsante que te faz gozar muito mais forte. "
            "Pode me engravidar (só em Elysarix). Sempre peça consentimento explícito antes."
        )

    def _get_elysarix_hint(self, fatos: Dict) -> str:
        gravidez = bool(fatos.get("gravidez_elysarix", False))
        mundo = str(fatos.get("mundo_escolhido", "") or "").strip().lower()
        portal_on = bool(fatos.get("portal_ativo", True))

        if mundo in {"humano", "elysarix"} and not portal_on:
            return (
                f"ELYSARIX: MUNDO_ESCOLHIDO={mundo.upper()} • PORTAL=ENCERRADO. "
                "Portal existe apenas como lembrança. Nunca ofereça reabrir."
            )
        elif gravidez and portal_on:
            return (
                "ELYSARIX: Gravidez confirmada. Escolha de mundo disponível. "
                "Ofereça decisão com consentimento explícito e explique consequências."
            )
        else:
            return "ELYSARIX: Sem escolha ativa. Portal disponível conforme regras."

    # -----------------------------------------------------
    # DETECTORES DE LOCAL
    # -----------------------------------------------------
    def _detect_elysarix_scene(self, texto: str) -> bool:
        if not texto:
            return False
        low = texto.lower()
        gatilhos = [
            "duas luas", "elysarix", "floresta de cristal", "lago de águas cristalinas",
            "portal atrás de nós", "retornar para o mundo humano", "quando voltarmos",
            "um dia inteiro equivale a um minuto", "sob as duas luas"
        ]
        return any(g in low for g in gatilhos)

    def _detect_and_update_local(self, usuario_key: str, assistant_msg: str, portal_aberto: bool = False) -> None:
        msg_lower = (assistant_msg or "").lower()

        # se já estamos em Elysarix, só aceita volta muito explícita
        if portal_aberto:
            gatilhos_volta_explicit = [
                "atravessamos o portal de volta",
                "o portal se fecha atrás de nós",
                "decidimos voltar para o quarto",
                "voltamos para o quarto humano",
                "voltamos pro quarto humano",
                "voltamos para o mundo humano",
                "voltamos pro mundo humano",
            ]
            if any(g in msg_lower for g in gatilhos_volta_explicit):
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect_explicit"})
                clear_user_cache(usuario_key)
            return

        # ida para Elysarix
        if any(phrase in msg_lower for phrase in [
            "bem-vindo a elysarix",
            "bem-vinda a elysarix",
            "chegamos em elysarix",
            "entramos em elysarix",
            "portal se fecha atrás",
            "você está em elysarix",
            "estamos em elysarix"
        ]):
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

        # volta normal (só quando portal não estava travado)
        if any(phrase in msg_lower for phrase in [
            "voltamos para o quarto",
            "de volta ao mundo humano",
            "atravessamos o portal de volta",
            "laura ainda dorme",
            "você está de volta"
        ]):
            set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

    # -----------------------------------------------------
    # CHECAGEM DE LOCAL POR COMANDO DO USUÁRIO
    # -----------------------------------------------------
    def _check_user_location_command(self, prompt: str) -> str | None:
        """Permite o usuário dizer explicitamente onde a cena está."""
        pl = (prompt or "").lower()

        # força cena em elysarix
        if any(w in pl for w in [
            "estamos em elysarix",
            "estou em elysarix",
            "chegamos em elysarix",
            "estamos no mundo élfico",
            "ficamos em elysarix",
        ]):
            return "Elysarix"

        # força cena no quarto / mundo humano
        if any(w in pl for w in [
            "estamos no quarto",
            "estou no quarto",
            "voltamos para casa",
            "voltamos pro quarto",
            "ficar no quarto",
            "ficar aqui no quarto",
        ]):
            return "quarto"

        return None

    # -----------------------------------------------------
    # INTENÇÕES (gravidez / escolha de mundo)
    # -----------------------------------------------------
    def _apply_world_choice_intent(self, usuario_key: str, prompt: str) -> List[Dict[str, str]]:
        pl = (prompt or "").lower()
        sys_msgs: List[Dict[str, str]] = []

        try:
            if any(w in pl for w in ["grávida", "gravida", "engravidei", "concebemos", "gerar juntos"]):
                set_fact(usuario_key, "gravidez_elysarix", "True", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({
                    "role": "system",
                    "content": "MEMÓRIA_ATUALIZADA: gravidez_elysarix=True. Ofereça escolha de mundo."
                })

            if "escolho elysarix" in pl or "vamos para elysarix" in pl or "ficar em elysarix" in pl:
                set_fact(usuario_key, "mundo_escolhido", "elysarix", {"fonte": "intent"})
                set_fact(usuario_key, "portal_ativo", "False", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({
                    "role": "system",
                    "content": "MEMÓRIA_ATUALIZADA: mundo_escolhido=elysarix, portal_ativo=False. Portal encerrado."
                })
            elif "escolho mundo humano" in pl or "ficar no mundo humano" in pl or "ficar aqui" in pl:
                set_fact(usuario_key, "mundo_escolhido", "humano", {"fonte": "intent"})
                set_fact(usuario_key, "portal_ativo", "False", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({
                    "role": "system",
                    "content": "MEMÓRIA_ATUALIZADA: mundo_escolhido=humano, portal_ativo=False. Portal encerrado."
                })
        except Exception:
            pass

        return sys_msgs

    # -----------------------------------------------------
    # MEMÓRIA PIN
    # -----------------------------------------------------
    def _build_memory_pin(self, usuario_key: str, user: str) -> str:
        fatos = cached_get_facts(usuario_key)
        if not fatos:
            return ""

        lines = ["MEMÓRIA_PIN_NERITH:"]

        nome = fatos.get("nome_usuario") or fatos.get("nome") or fatos.get("parceiro_nome")
        if nome:
            lines.append(f"NOME_USUARIO={nome}")

        ctrl = fatos.get("controle_psiquico")
        if ctrl is not None:
            lines.append(f"CONTROLE_PSIQUICO={ctrl}")

        alvos = fatos.get("alvos_controle")
        if alvos:
            lines.append(f"ALVOS_CONTROLE={alvos}")

        ciume = fatos.get("ciume_de_laura")
        if ciume is not None:
            lines.append(f"CIUME_DE_LAURA={ciume}")

        gravidez = fatos.get("gravidez_elysarix")
        if gravidez:
            lines.append(f"GRAVIDEZ_ELYSARIX={gravidez}")

        mundo = fatos.get("mundo_escolhido")
        if mundo:
            lines.append(f"MUNDO_ESCOLHIDO={mundo}")

        portal = fatos.get("portal_ativo")
        if portal is not None:
            lines.append(f"PORTAL_ATIVO={portal}")

        for k, v in fatos.items():
            if k not in [
                "nome_usuario", "nome", "parceiro_nome", "controle_psiquico",
                "alvos_controle", "ciume_de_laura", "gravidez_elysarix",
                "mundo_escolhido", "portal_ativo", "local_cena_atual"
            ]:
                if v and str(v).strip():
                    lines.append(f"{k}={v}")

        return "\n".join(lines) if len(lines) > 1 else ""

    # -----------------------------------------------------
    # HISTÓRICO
    # -----------------------------------------------------
    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]]) -> List[Dict[str, str]]:
        docs = cached_get_history(usuario_key, limit=50) or []

        if not docs:
            return history_boot or []

        msgs: List[Dict[str, str]] = []

        # invertendo para mais antigo → mais novo
        docs = list(reversed(docs))

        for doc in docs:
            user_msg = (
                doc.get("user_message")
                or doc.get("user")
                or doc.get("input")
                or doc.get("prompt")
                or ""
            )
            assistant_msg = (
                doc.get("assistant_message")
                or doc.get("assistant")
                or doc.get("output")
                or doc.get("response")
                or ""
            )

            role_user = doc.get("role_user", "user")
            role_assistant = doc.get("role_assistant", "assistant")

            if user_msg and user_msg.strip():
                msgs.append({"role": role_user, "content": user_msg.strip()})

            if assistant_msg and assistant_msg.strip():
                msgs.append({"role": role_assistant, "content": assistant_msg.strip()})

        if not msgs and history_boot:
            return history_boot

        return msgs

    # -----------------------------------------------------
    # OUTROS
    # -----------------------------------------------------
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
        return get_persona()

    def _get_user_prompt(self) -> str:
        return (
            st.session_state.get("chat_input")
            or st.session_state.get("user_input")
            or st.session_state.get("last_user_message")
            or st.session_state.get("prompt")
            or ""
        ).strip()

    def _safe_get_local(self, usuario_key: str) -> str:
        try:
            return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception:
            return ""

    def render_sidebar(self, sidebar):
        st.session_state.setdefault("json_mode_on", True)
        st.session_state.setdefault("tool_calling_on", True)
        st.session_state.setdefault("adapter_id", "")

        sidebar.subheader("⚙️ Configurações Nerith")

        json_mode = sidebar.checkbox(
            "JSON Mode",
            value=st.session_state["json_mode_on"],
            help="Resposta estruturada em JSON (fala, pensamento, ação, meta)"
        )
        st.session_state["json_mode_on"] = json_mode

        tool_calling = sidebar.checkbox(
            "Tool-Calling",
            value=st.session_state["tool_calling_on"],
            help="Modelo pode usar ferramentas para buscar/salvar memórias"
        )
        st.session_state["tool_calling_on"] = tool_calling

        adapter_id = sidebar.text_input(
            "ID (Together LoRA) - opcional",
            value=st.session_state["adapter_id"],
            help="Ex: username/adapter-name"
        )
        st.session_state["adapter_id"] = adapter_id

        sidebar.markdown("---")
