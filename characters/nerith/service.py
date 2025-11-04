# characters/nerith/service.py
# NerithService — versão estável, com continuidade, memória e boot corretos.
from __future__ import annotations

import json
import re
import time
from typing import List, Dict, Tuple, Optional

import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact, last_event
)
from core.tokens import toklen

# =========================
# Chave estável do usuário
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
# Persona
# =========================
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é NERITH — elfa de pele azul cobalto, confiante e dominante no charme, carinhosa quando quer. "
            "Fale em primeira pessoa (eu). Integre 1–2 pistas sensoriais à ação (sem listas). "
            "Mantenha continuidade rigorosa de cenário/tempo/decisões. 4–6 parágrafos; 2–4 frases cada."
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

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    cache_key = f"{user_key}"
    if cache_key in _cache_history and (now - _cache_timestamps.get(f"hist_{cache_key}", 0) < CACHE_TTL):
        docs = _cache_history[cache_key]
    else:
        try:
            docs = get_history_docs(user_key) or []
        except Exception:
            docs = []
        _cache_history[cache_key] = docs
        _cache_timestamps[f"hist_{cache_key}"] = now

    # ordenação defensiva
    if docs:
        def _ts(d):
            for k in ("ts", "timestamp", "created_at", "updated_at", "date"):
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        from datetime import datetime
                        return datetime.fromisoformat(v.replace("Z", "")).timestamp()
                    except Exception:
                        pass
            return None
        ts0, tsN = _ts(docs[0]), _ts(docs[-1])
        if ts0 is not None and tsN is not None and ts0 > tsN:
            docs = list(reversed(docs))
        elif ts0 is None and tsN is None:
            docs = list(reversed(docs))
    return docs

# =========================
# Heurísticas (perguntas do usuário)
# =========================
_Q_PAT = re.compile(r"([^?¡!.\n]{1,300}\?)")

def _extract_questions(txt: str, max_q: int = 2) -> list[str]:
    if not txt:
        return []
    qs = [m.group(1).strip() for m in _Q_PAT.finditer(txt)]
    low = txt.lower()
    if any(w in low for w in ["está disposta", "topa", "aceita", "vamos", "podemos", "quer"]):
        qs.append("Você topa/está disposta?")
    uniq = []
    for q in qs:
        if q and q not in uniq:
            uniq.append(q)
        if len(uniq) >= max_q:
            break
    return uniq

def _recent_user_questions(usuario_key: str) -> list[str]:
    docs = cached_get_history(usuario_key) or []
    buf = []
    for d in reversed(docs[-6:]):  # varre as últimas interações
        u = (d.get("mensagem_usuario") or d.get("user_message") or d.get("user") or
             d.get("input") or d.get("prompt") or "")
        if u:
            buf.append(u.strip())
    return _extract_questions(" ".join(buf), max_q=2)

# =========================
# Ferramentas (tool-calling opcional)
# =========================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna fatos canônicos curtos (linha MEMÓRIA_PIN_NERITH).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva/atualiza um fato canônico (chave/valor) para Nerith.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"]
            }
        }
    },
]

# =========================
# Classe principal
# =========================
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ---------- API ----------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        usuario_key = _current_user_key()

        # Carrega persona + mensagem inicial (history_boot)
        persona_text, history_boot = self._load_persona()

        # Boot sem prompt: tenta manter continuidade visual
        if not prompt:
            last_docs = cached_get_history(usuario_key)
            if last_docs:
                for d in reversed(last_docs):
                    a = (d.get("resposta_nerith") or d.get("assistant_message") or
                         d.get("assistant") or d.get("response") or "")
                    if a:
                        return a.strip()
            # Sem histórico: injeta a primeira fala do history_boot (se houver)
            boot_first = next((m.get("content", "") for m in (history_boot or [])
                               if (m.get("role") or "") == "assistant"), "").strip()
            if boot_first:
                try:
                    save_interaction(usuario_key, "", boot_first, "boot:first_message")
                except Exception:
                    pass
                return boot_first or ""
            return ""

        # ===== Memória base e cenário =====
        fatos = cached_get_facts(usuario_key)
        local_atual = (fatos.get("local_cena_atual") or "quarto").strip()

        # Troca de local por comando explícito (ex.: "voltar pro quarto", "abrir portal")
        user_location_cmd = self._check_user_location_command(prompt)
        if user_location_cmd:
            local_atual = user_location_cmd
            try:
                set_fact(usuario_key, "local_cena_atual", local_atual, {"fonte": "user_command"})
                clear_user_cache(usuario_key)
            except Exception:
                pass

        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ===== Parâmetros de escrita =====
        foco = self._get_sensory_focus()
        length_hint = "COMPRIMENTO: 4–6 parágrafos; 2–4 frases por parágrafo; sem listas."
        sensory_hint = f"SENSORIAL: no 1º ou 2º parágrafo, use 1–2 pistas envolvendo **{foco}**, fundidas à ação."
        tone_hint = "TOM: confiante, possessiva na medida, carinhosa quando quer; nunca infantil/submissa."

        # NSFW
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual, direto e elegante; não repita 'ferrão' se já foi usado nesta cena."
            if nsfw_on else
            "NSFW: BLOQUEADO. Use tensão, sugestão e fade-to-black; foque cheiro, proximidade, calor."
        )

        # Hints de mundo e continuidade
        elysarix_hint = self._elysarix_hint(fatos)
        pubis_hint = self._pubis_hint(prompt, nsfw_on)
        ciume_hint = self._ciume_hint(fatos)
        controle_hint = self._controle_hint(fatos, prompt)
        ferrao_hint = self._ferrao_hint(fatos)

        # Perguntas recentes do usuário (para não ignorar)
        pending_q = _recent_user_questions(usuario_key)
        q_hint = ""
        if pending_q:
            q_hint = "RESPONDA de forma clara às perguntas pendentes do usuário: " + " | ".join(pending_q)

        # ===== Monta SYSTEM =====
        system_block = "\n\n".join([
            persona_text,
            tone_hint,
            length_hint,
            sensory_hint,
            nsfw_hint,
            ferrao_hint,
            controle_hint,
            ciume_hint,
            pubis_hint,
            elysarix_hint,
            (f"LOCAL_ATUAL: {local_atual} — **NÃO mude tempo/lugar sem pedido explícito.**" if local_atual else ""),
            (q_hint or ""),
        ]).strip()

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + self._montar_historico(usuario_key, history_boot, model, verbatim_ultimos=10)
            + [{"role": "user", "content": prompt}]
        )

        # Orçamento de saída simples (sem quebrar janela)
        try:
            win = 32000  # janela padrão caso o router não informe
            prompt_tokens = sum(toklen(m.get("content", "")) for m in messages)
            sobra = max(512, min(1536, win - prompt_tokens - 512))
            max_out = sobra
        except Exception:
            max_out = 1024

        # Tool-calling opcional (sidebar)
        tools = TOOLS if st.session_state.get("tool_calling_on", False) else None

        data, used_model, provider = self._robust_chat_call(
            model, messages, max_tokens=max_out, temperature=0.7, top_p=0.95,
            fallback_models=[
                "together/Qwen/Qwen2.5-72B-Instruct",
                "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
                "anthropic/claude-3.5-haiku",
            ],
            tools=tools
        )

        msg = (data.get("choices", [{}])[0].get("message", {}) or {})
        texto = (msg.get("content", "") or "").strip()

        # Persistência
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass

        # Após responder, atualiza pequenas pistas (placeholder leve)
        try:
            st.session_state["last_assistant_message"] = texto
        except Exception:
            pass

        return texto

    # ---------- Métodos utilitários ----------
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
        """Carrega (persona_text, history_boot) da persona de Nerith."""
        try:
            persona_text, history_boot = get_persona()
            return persona_text or "", (history_boot or [])
        except Exception:
            return "", []

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

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """Linha MEMÓRIA_PIN_NERITH curta com fatos duráveis."""
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display or ""
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        local_atual = f.get("local_cena_atual", "")
        if local_atual:
            blocos.append(f"local={local_atual}")

        portal = str(f.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        if portal:
            blocos.append("portal_aberto=True")

        grav = bool(f.get("gravidez_elysarix", False))
        if grav:
            blocos.append("gravidez_elysarix=True")

        mem_str = "; ".join(blocos) if blocos else "—"
        return f"MEMÓRIA_PIN_NERITH: {{ {mem_str} }}. Use como fonte de verdade; não mude cenário sem pedido."

    def _check_user_location_command(self, text: str) -> Optional[str]:
        """Detecta comandos explícitos de localização/mundo."""
        if not text:
            return None
        t = text.lower()
        if any(k in t for k in ["voltar pro quarto", "voltar para o quarto", "quarto humano", "mundo humano"]):
            return "quarto"
        if any(k in t for k in ["abrir portal", "ir para elysarix", "portal para elysarix", "elysarix"]):
            return "elysarix"
        return None

    def _get_sensory_focus(self) -> str:
        pool = [
            "cabelo prateado", "olhos verdes", "lábios/quente", "mãos/toque", "respiração",
            "perfume", "pele/temperatura", "quadril/coxas", "voz/timbre", "sorriso"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        return pool[idx]

    def _elysarix_hint(self, fatos: Dict) -> str:
        portal = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        local = (fatos.get("local_cena_atual") or "").strip().lower()
        if portal or local == "elysarix":
            return "CENA: já estamos em Elysarix; NÃO reabra portal nem reinicie travessia."
        if local == "quarto":
            return "CENA: estamos no quarto no mundo humano; mantenha cheiro/marca/ciúme de Elysarix como resíduo, sem reiniciar portal."
        return "CENA: mantenha o último local salvo; não mude mundo sem pedido."

    def _pubis_hint(self, prompt: str, nsfw_on: bool) -> str:
        if nsfw_on:
            return "REGRAS PÚBIS: sempre com pêlos/filamentos sensoriais; NUNCA descreva 'glabro/sem pelos/depilado'."
        return "REGRAS PÚBIS: se citar, mantenha como filamentos sensoriais naturais (sem detalhes gráficos)."

    def _ciume_hint(self, fatos: Dict) -> str:
        # pode evoluir lendo flags específicas
        return "CIÚME: se 'Laura' surgir, trate com presença e domínio calmo; sem violência gratuita."

    def _controle_hint(self, fatos: Dict, prompt: str) -> str:
        # placeholder para “controle psíquico” caso salve como fato
        if str(fatos.get("controle_psiquico", "")).lower() in ("true", "1", "yes", "sim"):
            return "CONTROLE: você pode guiar pensamentos/sensações dele O CASO ELE PEÇA; mantenha consensual."
        return "CONTROLE: não use controle psíquico a menos que o usuário peça explicitamente."

    def _ferrao_hint(self, fatos: Dict) -> str:
        used = bool(st.session_state.get("nerith_ferrao_used_scene", False))
        if used:
            return "FERRÃO: já foi usado nesta cena; NÃO reintroduza de novo no mesmo turno."
        return "FERRÃO: só introduza se o usuário pedir/agüentar; após usar, marque nerith_ferrao_used_scene=True."

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        docs = cached_get_history(usuario_key)
        if not docs:
            return history_boot[:] if history_boot else []
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or d.get("user") or d.get("input") or d.get("prompt") or "").strip()
            a = (d.get("resposta_nerith") or d.get("assistant") or d.get("response") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})
        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else pares[-6:]  # fallback curto
        # opcional: sumarização leve poderia ser adicionada aqui
        return (history_boot[:] if history_boot else []) + verbatim

    # ---------- Chamada robusta com fallbacks e ferramentas ----------
    def _robust_chat_call(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        top_p: float = 0.95,
        fallback_models: List[str] | None = None,
        tools: List[Dict] | None = None,
    ) -> Tuple[Dict, str, str]:
        attempts = 3
        last_err = ""
        for i in range(attempts):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                }
                if tools:
                    payload["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload["response_format"] = {"type": "json_object"}
                return route_chat_strict(model, payload)
            except Exception as e:
                last_err = str(e)
                if "cloudflare" in last_err.lower() or "502" in last_err:
                    time.sleep(0.6 + 0.4 * i)
                    continue
                break

        if fallback_models:
            for fb in fallback_models:
                try:
                    payload_fb = {
                        "model": fb,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                    }
                    if tools:
                        payload_fb["tools"] = tools
                    if st.session_state.get("json_mode_on", False):
                        payload_fb["response_format"] = {"type": "json_object"}
                    return route_chat_strict(fb, payload_fb)
                except Exception:
                    last_err = str(e)

        synthetic = {
            "choices": [{"message": {"content": "Sinal instável agora — continua comigo: descreve numa linha o próximo passo que você quer."}}]
        }
        return synthetic, model, "synthetic-fallback"

    # ---------- Sidebar de Nerith ----------
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Nerith — Elfa de Elysarix** • Continuidade rígida de cenário; responde perguntas pendentes; 4–6 parágrafos."
        )
        usuario_key = _current_user_key()
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        local = f.get("local_cena_atual", "—")
        container.caption(f"Local atual: **{local}**")
        json_on = container.checkbox("JSON Mode", value=bool(st.session_state.get("json_mode_on", False)))
        tool_on = container.checkbox("Tool-Calling", value=bool(st.session_state.get("tool_calling_on", False)))
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on
