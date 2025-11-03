# characters/nerith/service.py
# NerithService — versão consolidada com Q-First (responder perguntas primeiro)
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import List, Dict, Tuple

import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact, last_event
)
from core.tokens import toklen

# =========================
# Chave estável
# =========================
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid or 'anon'}::nerith"

# =========================
# NSFW (opcional)
# =========================
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# =========================
# Persona específica
# =========================
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é NERITH — elfa de pele azulada, confiante e dominante no charme. "
            "Fale em primeira pessoa. Integre pistas sensoriais à ação (sem listas). "
            "Mantenha continuidade de cena/tempo. 4–7 parágrafos; 2–4 frases por parágrafo."
        )
        return txt, []

# =========================
# Cache leve (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos

_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_timestamps.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_timestamps.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_timestamps.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None)
            _cache_timestamps.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)
    # remover variantes de history cache
    for ck in list(_cache_history.keys()):
        if ck.startswith(user_key):
            _cache_history.pop(ck, None)
            _cache_timestamps.pop(f"hist_{ck}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_timestamps.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f
    _cache_timestamps[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str, limit: int | None = None) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    cache_key = f"{user_key}" if limit is None else f"{user_key}__lim_{limit}"
    if cache_key in _cache_history and (now - _cache_timestamps.get(f"hist_{cache_key}", 0) < CACHE_TTL):
        docs = _cache_history[cache_key]
    else:
        try:
            docs = get_history_docs(user_key, limit=limit) if limit else get_history_docs(user_key)
            docs = docs or []
        except Exception:
            docs = []
        _cache_history[cache_key] = docs
        _cache_timestamps[f"hist_{cache_key}"] = now

    def _extract_ts(d: Dict) -> float | None:
        for k in ("ts", "timestamp", "created_at", "updated_at", "date"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v.replace("Z", "")).timestamp()
                except Exception:
                    pass
        return None

    if docs:
        ts0 = _extract_ts(docs[0])
        tsN = _extract_ts(docs[-1])
        if ts0 is not None and tsN is not None and ts0 > tsN:
            docs = list(reversed(docs))
        elif ts0 is None and tsN is None:
            docs = list(reversed(docs))
    return docs

# =========================
# Ferramentas (tool-calling)
# =========================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Busca memória canônica curta de Nerith (linha compacta).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva/atualiza um fato canônico (chave/valor) para Nerith.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fact",
            "description": "Lê um fato canônico específico (chave).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
]

# =========================
# Util: detectar perguntas
# =========================
_Q_PAT = re.compile(r"([^?¡!.\n]{1,300}\?)")

def _extract_questions_from_text(txt: str, max_q: int = 3) -> list[str]:
    """Extrai até N perguntas simples de um texto."""
    if not txt:
        return []
    qs = [m.group(1).strip() for m in _Q_PAT.finditer(txt)]
    # também pega padrões sem '?' explícito (sim/não)
    low = txt.lower()
    heur = []
    if any(w in low for w in ["está disposta", "topa", "aceita", "vamos", "podemos", "quer"]):
        heur.append("Você topa/está disposta?")
    # unir e limitar
    out = []
    for q in qs + heur:
        if q and q not in out:
            out.append(q)
        if len(out) >= max_q:
            break
    return out

def _recent_user_questions(usuario_key: str, max_chars: int = 600) -> list[str]:
    """Vasculha últimas falas do usuário no histórico e extrai perguntas recentes."""
    docs = cached_get_history(usuario_key) or []
    snippets = []
    for d in reversed(docs):
        u = (
            d.get("mensagem_usuario") or d.get("user_message") or d.get("user")
            or d.get("input") or d.get("prompt") or ""
        )
        u = (u or "").strip()
        if u:
            snippets.append(u)
        if len(" ".join(snippets)) >= max_chars:
            break
    return _extract_questions_from_text(" ".join(snippets))

# =========================
# Serviço principal
# =========================
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ---------- API ----------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        persona_text, history_boot = self._load_persona()
        usuario_key = _current_user_key()

        fatos_existentes = cached_get_facts(usuario_key)
        local_registrado = (fatos_existentes.get("local_cena_atual") or "").lower()
        portal_registrado = str(fatos_existentes.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        existing_history = cached_get_history(usuario_key, limit=1)

        # -------- Sem prompt: boot/continua última --------
        if not prompt:
            if existing_history:
                last_assistant = existing_history[0].get("assistant_message", "") or \
                                 existing_history[0].get("assistant", "") or ""
                last_user = existing_history[0].get("user_message", "") or \
                            existing_history[0].get("user", "") or ""
                return (last_assistant or last_user or "").strip()

            boot_text = (history_boot[0].get("content", "") if (history_boot and len(history_boot) > 0)
                         else "A porta do guarda-roupas se abre sozinha. A luz azul me revela. Eu te encontrei.")
            save_interaction(usuario_key, "", boot_text, "system:boot")
            if portal_registrado or local_registrado == "elysarix":
                set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "boot-preserva"})
            else:
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "boot"})
            clear_user_cache(usuario_key)
            return boot_text

        # -------- Toggles --------
        tool_calling_on = bool(st.session_state.get("tool_calling_on", False))
        tools = TOOLS if tool_calling_on else None
        max_iterations = 3 if tool_calling_on else 1

        # -------- Intenções & comandos de local --------
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)
        user_location = self._check_user_location_command(prompt)
        if user_location:
            set_fact(usuario_key, "local_cena_atual", user_location, {"fonte": "user_command"})
            clear_user_cache(usuario_key)

        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, st.session_state.get("user_id", "") or "")

        fatos = cached_get_facts(usuario_key)
        portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        if portal_aberto and (not local_atual or local_atual.lower() != "elysarix"):
            local_atual = "Elysarix"
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "reidrata_toggle"})
            clear_user_cache(usuario_key)

        def _safe_int(v, default=1):
            try:
                return int(v if str(v).strip() else default)
            except Exception:
                return default
        dreamworld_detail_level = _safe_int(fatos.get("dreamworld_detail_level", 1), 1)
        guide_assertiveness = _safe_int(fatos.get("guide_assertiveness", 1), 1)

        # -------- Foco sensorial --------
        foco = self._get_sensory_focus()

        # -------- Hints principais --------
        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo {foco}, fundidas à ação."
        tone_hint = "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil."

        # -------- NSFW --------
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo; descreva com elegância."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte, tensão e fade-to-black."
        )

        # -------- Hints contextuais --------
        pubis_hint = self._get_pubis_hint(prompt, nsfw_on)
        controle_hint = self._get_controle_hint(fatos, prompt)
        ciume_hint = self._get_ciume_hint(fatos)
        ferrao_hint = self._get_ferrao_hint()
        elysarix_hint = self._get_elysarix_hint(fatos)
        if portal_aberto:
            elysarix_hint += "\n⚠️ Já estamos em Elysarix — não repita a travessia nem a introdução. Continue exatamente do ponto atual."

        # -------- “continue/continua/segue...” --------
        continue_pre_msgs: List[Dict[str, str]] = []
        p_low = prompt.lower().strip()
        if p_low in ("continue", "continua", "segue", "prossegue", "continua nerith", "continue nerith"):
            if portal_aberto:
                continue_pre_msgs.append({
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário disse apenas para continuar — mantenha exatamente o ponto atual em ELYSARIX, sem mudar cenário/tempo."
                })
            else:
                continue_pre_msgs.append({
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário disse apenas para continuar — mantenha exatamente o ponto atual, sem mudar cenário/tempo."
                })

        # -------- Perguntas do usuário (Q-First) --------
        # perguntas do prompt atual
        q_now = _extract_questions_from_text(prompt, max_q=3)
        # perguntas recentes do histórico (caso o prompt atual não traga '?')
        q_recent = _recent_user_questions(usuario_key, max_chars=400) if not q_now else []
        user_questions = q_now or q_recent
        q_focus_msg = []
        if user_questions:
            joined = " | ".join(user_questions[:3])
            q_focus_msg = [{
                "role": "system",
                "content": (
                    "QUESTION_FOCUS: responda explicitamente às PERGUNTAS DO USUÁRIO nas primeiras 1–2 frases, "
                    f"antes de continuar a cena. Perguntas detectadas: {joined}"
                )
            }]
            # opcional: marcador visual
            st.session_state["nerith_last_questions"] = user_questions

        # -------- Bloco SYSTEM consolidado --------
        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint,
            nsfw_hint, ferrao_hint, controle_hint, ciume_hint,
            pubis_hint, elysarix_hint,
            "Q-FIRST: Se o usuário fez pergunta(s), responda primeiro, em 1–2 frases objetivas. "
            "Depois retome a narrativa mantendo o cenário/tempo e decisões anteriores.",
            "FERRAMENTAS: use get_memory_pin (estado), get_fact (checar portal) e set_fact (marcar portal_aberto=True ao mudar para Elysarix). "
            "Nunca repita a travessia se portal_aberto=True."
        ])

        # -------- ÂNCORA de continuidade --------
        anchor_msgs: List[Dict[str, str]] = []
        try:
            docs_tmp = cached_get_history(usuario_key) or []
            last_assistant, last_user = "", ""
            for d in reversed(docs_tmp):
                if not last_assistant:
                    last_assistant = (
                        d.get("resposta_nerith") or d.get("assistant_message") or
                        d.get("assistant") or d.get("response") or ""
                    )
                if not last_user:
                    last_user = (
                        d.get("mensagem_usuario") or d.get("user_message") or
                        d.get("user") or d.get("input") or d.get("prompt") or ""
                    )
                if last_assistant and last_user:
                    break
            if last_assistant:
                anchor_msgs.append({
                    "role": "system",
                    "content": f"ÂNCORA_CONTINUIDADE: Última fala de Nerith (resumo curto): {last_assistant[:280]}"
                })
            if last_user:
                anchor_msgs.append({
                    "role": "system",
                    "content": f"ÂNCORA_CONTINUIDADE: Última fala do usuário (resumo curto): {last_user[:280]}"
                })
        except Exception:
            pass

        # -------- Histórico --------
        hist_msgs = self._montar_historico(usuario_key, history_boot)

        # -------- Montagem de messages --------
        pre_msgs: List[Dict[str, str]] = []
        if state_msgs:
            pre_msgs.extend(state_msgs)
        if continue_pre_msgs:
            pre_msgs.extend(continue_pre_msgs)
        if q_focus_msg:
            pre_msgs.extend(q_focus_msg)

        messages: List[Dict[str, str]] = (
            pre_msgs
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + anchor_msgs
            + [{
                "role": "system",
                "content": f"LOCAL_ATUAL: {local_atual or '—'}. Regra dura: NÃO mude o cenário sem pedido explícito."
            }]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # -------- Loop tool-calling --------
        iteration = 0
        texto = ""
        while iteration < max_iterations:
            iteration += 1

            if tool_calling_on:
                with st.spinner(f"🤖 Processando (iteração {iteration}/{max_iterations})..."):
                    data, used_model, provider = self._robust_chat_call(model, {
                        "model": model,
                        "messages": messages,
                        "max_tokens": 1536,
                        "temperature": 0.7,
                        "top_p": 0.95,
                    }, tools=tools)
            else:
                data, used_model, provider = self._robust_chat_call(model, {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 1536,
                    "temperature": 0.7,
                    "top_p": 0.95,
                })

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls:
                save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
                self._detect_and_update_local(usuario_key, texto, portal_aberto=portal_aberto)
                clear_user_cache(usuario_key)
                if self._detect_elysarix_scene(texto):
                    set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
                    clear_user_cache(usuario_key)
                try:
                    self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
                except Exception:
                    pass
                try:
                    st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
                    st.session_state["last_assistant_message"] = texto
                except Exception:
                    pass
                return texto

            messages.append({
                "role": "assistant",
                "content": texto or None,
                "tool_calls": tool_calls
            })
            if tool_calling_on:
                st.info(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")

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
                    st.success(f"  ✓ {func_name}: {str(result)[:80]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": func_name,
                    "content": result
                })

            if iteration >= max_iterations and tool_calls:
                st.warning("⚠️ Limite de iterações de Tool Calling atingido. Finalizando…")
                texto_final = texto or "Desculpe, não consegui completar a operação."
                save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
                self._detect_and_update_local(usuario_key, texto_final, portal_aberto=portal_aberto)
                clear_user_cache(usuario_key)
                if self._detect_elysarix_scene(texto_final):
                    set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
                    clear_user_cache(usuario_key)
                try:
                    self._update_rolling_summary_v2(usuario_key, model, prompt, texto_final)
                except Exception:
                    pass
                return texto_final

        save_interaction(usuario_key, prompt, texto, "fallback")
        return texto

    # ---------- Infra ----------
    def _robust_chat_call(self, model: str, payload: Dict, tools: List[Dict] | None = None) -> Tuple[Dict, str, str]:
        if tools:
            payload["tools"] = tools
        if st.session_state.get("json_mode_on", False):
            payload["response_format"] = {"type": "json_object"}
        adapter_id = (st.session_state.get("adapter_id") or "").strip() or (st.session_state.get("together_lora_id") or "").strip()
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

    def _exec_tool_call(self, tool_name: str, args: Dict, usuario_key: str, user: str) -> str:
        try:
            if tool_name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, st.session_state.get("user_id", "") or "")
            elif tool_name == "set_fact":
                key = (args or {}).get("key", "")
                value = (args or {}).get("value", "")
                if not key:
                    return "ERRO: chave vazia"
                set_fact(usuario_key, key, value, {"fonte": "tool_call"})
                clear_user_cache(usuario_key)
                return f"OK: {key}={value}"
            elif tool_name == "get_fact":
                k = (args or {}).get("key", "")
                if not k:
                    return "ERRO: chave vazia"
                val = get_fact(usuario_key, k, "")
                return f"{k}={val}" if val else f"{k}=<não encontrado>"
            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ---------- Hints dinâmicos ----------
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
        pl = (prompt or "").lower()
        pubis_terms = {"púbis", "pubis", "púbico", "pubico", "pêlos", "pelos", "penugem", "pelinhos"}
        citou_pubis = any(t in pl for t in pubis_terms)
        if not citou_pubis:
            return ""
        if nsfw_on:
            return (
                "PUBIS_HINT: o usuário citou púbis/pêlos. "
                "Descreva meus **pêlos/filamentos sensoriais** reagindo ao toque com linguagem elegante. "
                "**Nunca** diga que é glabro/sem pelos."
            )
        else:
            return (
                "PUBIS_HINT: o usuário citou púbis/pêlos. "
                "Mencione discretamente meus **pêlos/filamentos sensoriais** — sem ato explícito. "
                "**Nunca** diga que é glabro/sem pelos."
            )

    def _get_controle_hint(self, fatos: Dict, prompt: str) -> str:
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        if not ctrl_on:
            return "CONTROLE_PSIQUICO: DESATIVADO."
        alvos = str(fatos.get("alvos_controle", "pessoas próximas")).strip()
        pl = (prompt or "").lower()
        contexto_remoto = any(w in pl for w in ["trabalho", "escritorio", "escritório", "reuni", "faculdade", "aula"])
        hint = (
            f"CONTROLE_PSIQUICO: ATIVO. Quando distante, posso falar através de {alvos} com sutileza (micro-pausa, olhar fixo)."
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
            "FERRAO_ONIRICO: quando transamos de quatro e houver consentimento explícito, posso usar meu ferrão. "
            "É quente/pulsante e intensifica o prazer. Só pode engravidar em Elysarix."
        )

    def _get_elysarix_hint(self, fatos: Dict) -> str:
        gravidez = bool(fatos.get("gravidez_elysarix", False))
        mundo = str(fatos.get("mundo_escolhido", "") or "").strip().lower()
        portal_on = bool(fatos.get("portal_ativo", True))
        if mundo in {"humano", "elysarix"} and not portal_on:
            return (
                f"ELYSARIX: MUNDO_ESCOLHIDO={mundo.upper()} • PORTAL=ENCERRADO. "
                "Portal existe apenas como lembrança — não ofereça reabrir."
            )
        elif gravidez and portal_on:
            return (
                "ELYSARIX: Gravidez confirmada. Escolha de mundo disponível. "
                "Ofereça decisão com consentimento explícito e explique consequências."
            )
        else:
            return "ELYSARIX: Sem escolha ativa. Portal disponível conforme regras."

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

    # ---------- Sidebar ----------
    def render_sidebar(self, sidebar):
        st.session_state.setdefault("json_mode_on", True)
        st.session_state.setdefault("tool_calling_on", True)
        st.session_state.setdefault("adapter_id", "")
        sidebar.subheader("⚙️ Configurações — Nerith")
        json_mode = sidebar.checkbox("JSON Mode", value=bool(st.session_state["json_mode_on"]))
        st.session_state["json_mode_on"] = json_mode
        tool_calling = sidebar.checkbox("Tool-Calling", value=bool(st.session_state["tool_calling_on"]))
        st.session_state["tool_calling_on"] = tool_calling
        adapter_id = sidebar.text_input("Adapter ID (Together LoRA) — opcional", value=st.session_state["adapter_id"])
        st.session_state["adapter_id"] = adapter_id
        debug = sidebar.checkbox("Debug de Memória (legenda)", value=bool(st.session_state.get("nerith_debug_mem", False)))
        st.session_state["nerith_debug_mem"] = debug
        sidebar.markdown("---")
        if debug:
            usuario_key = _current_user_key()
            try:
                f = cached_get_facts(usuario_key) or {}
            except Exception:
                f = {}
            rs = (self._get_rolling_summary(usuario_key) or "")[:240]
            sidebar.caption(f"User key: `{usuario_key}`")
            sidebar.caption(f"Resumo rolante (len={len(rs)}): {rs}")
            sidebar.caption(f"Local atual: {self._safe_get_local(usuario_key) or '—'}")
            if not f:
                sidebar.caption("facts: (vazio)")
            else:
                with sidebar.expander("facts (parcial)"):
                    c = 0
                    for k, v in f.items():
                        vv = str(v)
                        if len(vv) > 120:
                            vv = vv[:120] + "…"
                        sidebar.write(f"- **{k}** = {vv}")
                        c += 1
                        if c >= 30:
                            sidebar.write("…")
                            break
            last_qs = st.session_state.get("nerith_last_questions", [])
            if last_qs:
                sidebar.caption("Perguntas detectadas:")
                for q in last_qs:
                    sidebar.write(f"• {q}")

    # ---------- Aux ----------
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

    def _detect_and_update_local(self, usuario_key: str, assistant_msg: str, portal_aberto: bool = False):
        msg_lower = (assistant_msg or "").lower()
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
        if any(phrase in msg_lower for phrase in [
            "bem-vindo a elysarix", "bem-vinda a elysarix",
            "chegamos em elysarix", "entramos em elysarix",
            "portal se fecha atrás", "você está em elysarix", "estamos em elysarix"
        ]):
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return
        if any(phrase in msg_lower for phrase in [
            "voltamos para o quarto", "de volta ao mundo humano",
            "atravessamos o portal de volta", "laura ainda dorme", "você está de volta"
        ]):
            set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

    def _check_user_location_command(self, prompt: str) -> str | None:
        pl = (prompt or "").lower()
        if any(w in pl for w in ["estamos em elysarix", "estou em elysarix", "chegamos em elysarix"]):
            return "Elysarix"
        if any(w in pl for w in ["estamos no quarto", "estou no quarto", "voltamos para casa", "voltamos pro quarto"]):
            return "quarto"
        return None

    def _apply_world_choice_intent(self, usuario_key: str, prompt: str) -> List[Dict[str, str]]:
        pl = (prompt or "").lower()
        sys_msgs: List[Dict[str, str]] = []
        try:
            if any(w in pl for w in ["grávida", "gravida", "engravidei", "concebemos", "gerar juntos"]):
                set_fact(usuario_key, "gravidez_elysarix", "True", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({"role": "system", "content": "MEMÓRIA_ATUALIZADA: gravidez_elysarix=True. Ofereça escolha de mundo."})
            if "escolho elysarix" in pl or "vamos para elysarix" in pl or "ficar em elysarix" in pl:
                set_fact(usuario_key, "mundo_escolhido", "elysarix", {"fonte": "intent"})
                set_fact(usuario_key, "portal_ativo", "False", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({"role": "system", "content": "MEMÓRIA_ATUALIZADA: mundo_escolhido=elysarix, portal_ativo=False. Portal encerrado."})
            elif "escolho mundo humano" in pl or "ficar no mundo humano" in pl or "ficar aqui" in pl:
                set_fact(usuario_key, "mundo_escolhido", "humano", {"fonte": "intent"})
                set_fact(usuario_key, "portal_ativo", "False", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({"role": "system", "content": "MEMÓRIA_ATUALIZADA: mundo_escolhido=humano, portal_ativo=False. Portal encerrado."})
        except Exception:
            pass
        return sys_msgs

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []
        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")
        casados = bool(f.get("casados", True))
        blocos.append(f"casados={casados}")
        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")
        mundo = str(f.get("mundo_escolhido", "") or "").strip()
        if mundo:
            blocos.append(f"mundo_escolhido={mundo}")
        if "portal_aberto" in f:
            blocos.append(f"portal_aberto={f.get('portal_aberto')}")
        mem_str = "; ".join(blocos) if blocos else "—"
        return (
            "MEMÓRIA_PIN: "
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Regra: manter continuidade estrita de tempo/lugar e decisões anteriores."
        )

    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]]) -> List[Dict[str, str]]:
        docs = cached_get_history(usuario_key) or []
        if not docs:
            return history_boot[:] if history_boot else []
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (
                d.get("mensagem_usuario") or d.get("user_message") or
                d.get("user") or d.get("input") or d.get("prompt") or ""
            )
            a = (
                d.get("resposta_nerith") or d.get("assistant_message") or
                d.get("assistant") or d.get("response") or d.get("output") or ""
            )
            if u and u.strip():
                pares.append({"role": "user", "content": u.strip()})
            if a and a.strip():
                pares.append({"role": "assistant", "content": a.strip()})
        return pares if pares else (history_boot[:] if history_boot else [])

    # ---------- Rolling summary ----------
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("nerith.rs.v2", "") or f.get("nerith.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("nerith.rs.v2", "")
            last_update_ts = float(f.get("nerith.rs.v2.ts", 0))
            now = time.time()
            if not last_summary:
                return True
            if now - last_update_ts > 300:
                return True
            if (len(last_user) + len(last_assistant)) > 100:
                return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant):
            return
        seed = (
            "Resuma a conversa recente em 6–10 frases telegráficas, apenas fatos duráveis: "
            "nomes, relação, local/tempo atual, itens/gestos fixos e rumo do enredo. Proíba diálogos literais."
        )
        try:
            data, used_model, provider = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"USER:\n{last_user}\n\nNERITH:\n{last_assistant}"}
                ],
                "max_tokens": 180,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "nerith.rs.v2", resumo, {"fonte": "auto_summary"})
                set_fact(usuario_key, "nerith.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    # ---------- UI helpers ----------
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — fala baixinho no meu ouvido."
        return "Mantém o cenário e dá o próximo passo com calma."
