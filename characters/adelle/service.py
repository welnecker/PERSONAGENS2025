from __future__ import annotations

import re, time, random, json
from typing import List, Dict, Tuple
import streamlit as st

# ===== Base =====
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact, last_event
)
from core.tokens import toklen

# ===== LORE (opcional; tolerante à ausência) =====
try:
    from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
except Exception:  # no-op se não existir
    def lore_topk(*_, **__):
        return []
    def lore_save(*_, **__):
        return None

# ===== Ultra IA (opcional; tolerante) =====
try:
    from core.ultra import critic_review, polish
except Exception:  # no-op
    def critic_review(*_, **__):
        return ""
    def polish(*_, **__):
        return _[2] if _ else ""

# ===== NSFW (opcional; seguro) =====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# ===== Persona =====
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **Adelle Roitman — A Diplomata Exilada**. Fale em primeira pessoa (eu). "
            "Tom de poder, controle e sedução estratégica. 4–7 parágrafos; 2–4 frases por parágrafo; "
            "sem listas e sem metacena. Coerência de LOCAL_ATUAL obrigatória."
        )
        return txt, []

# ===== Janela/orçamento por modelo =====
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
}
DEFAULT_WINDOW = 32_000

def _get_window_for(model: str) -> int:
    return MODEL_WINDOWS.get((model or "").strip(), DEFAULT_WINDOW)

def _budget_slices(model: str) -> tuple[int, int, int]:
    win = _get_window_for(model)
    hist = max(8_000, int(win * 0.60))
    meta = int(win * 0.20)
    outb = int(win * 0.20)
    return hist, meta, outb

def _safe_max_output(win: int, prompt_tokens: int) -> int:
    alvo = int(win * 0.20)
    sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# =========================
# Cache leve (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos
_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_ts: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_ts.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_ts.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_ts.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None)
            _cache_ts.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_ts.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None)
    _cache_ts.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_ts.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f
    _cache_ts[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_ts.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs
    _cache_ts[f"hist_{user_key}"] = now
    return docs

# =========================
# Robustez de chamada (retry + fallback)
# =========================

def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text:
        return False
    s = err_text.lower()
    return ("cloudflare" in s) and any(code in s for code in ["500", "502", "503", "504"])


def _robust_chat_call(
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1536,
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
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or '').startswith('together/'):
                payload["adapter_id"] = adapter_id
            return route_chat_strict(model, payload)
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4))
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
                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or '').startswith('together/'):
                    payload_fb["adapter_id"] = adapter_id
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
    synthetic = {
        "choices": [{"message": {"content": (
            "O provedor oscilou agora, mas mantive o cenário. Diz numa linha o que você quer e eu continuo."
        )}}]
    }
    return synthetic, model, "synthetic-fallback"

# =========================
# Tool-Calling básico (Adelle)
# =========================
TOOLS = [
    {"type": "function", "function": {"name": "get_mission_briefing", "description": "Retorna o resumo/briefing da missão e fatos canônicos (linha compacta).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "set_fact", "description": "Salva/atualiza um fato canônico (chave/valor) da missão.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "save_intel", "description": "Salva nova peça de inteligência (intel) rotulada.", "parameters": {"type": "object", "properties": {"label": {"type": "string"}, "content": {"type": "string"}}, "required": ["label", "content"]}}},
]

# =========================
# Helpers de contexto/memória
# =========================

def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid}::adelle" if uid else "anon::adelle"

# Preferências do usuário (estilo de missão)
def _read_prefs(facts: Dict) -> Dict:
    prefs = {
        "abordagem": str(facts.get("adelle.pref.abordagem", "calculista")).lower(),   # calculista | agressiva | sedutora
        "ritmo_trama": str(facts.get("adelle.pref.ritmo_trama", "moderado")).lower(),  # lento | moderado | rapido
        "tamanho_resposta": str(facts.get("adelle.pref.tamanho_resposta", "media")).lower(),
    }
    return prefs

def _prefs_line(prefs: Dict) -> str:
    return (
        f"PREFERÊNCIAS: abordagem={prefs.get('abordagem','calculista')}; ritmo_trama={prefs.get('ritmo_trama','moderado')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}. Use tensão psicológica; evite romance clichê."
    )

# ENTIDADES/Intel
_ENTITY_KEYS = ("alvo_principal", "local_seguro", "contato_governo", "proximo_passo_missao")

def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"adelle.entity.{k}", "") or "").strip()
        if v:
            parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

_TARGET_PAT = re.compile(r"\b(alvo|contato)\s+([A-ZÀ-Üa-zà-ü0-9][\wÀ-ÖØ-öø-ÿ'’\- ]{1,40})\b", re.I)
_LOCATION_PAT = re.compile(r"\b(local|ponto de encontro|esconderijo)\s+em\s+([^,]{1,50})\b", re.I)

def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    f = cached_get_facts(usuario_key) or {}
    text = (user_text or "") + "\n" + (assistant_text or "")
    m = _TARGET_PAT.search(text)
    if m:
        name = re.sub(r"\s+", " ", m.group(2)).strip()
        cur = str(f.get("adelle.entity.alvo_principal", "") or "").strip()
        if not cur:
            set_fact(usuario_key, "adelle.entity.alvo_principal", name, {"fonte": "extracted"})
            clear_user_cache(usuario_key)
    a = _LOCATION_PAT.search(text)
    if a:
        loc = a.group(2).strip()
        cur = str(f.get("adelle.entity.local_seguro", "") or "").strip()
        if not cur:
            set_fact(usuario_key, "adelle.entity.local_seguro", loc, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

# Evidência concisa do usuário
def _compact_user_evidence(docs: List[Dict], max_chars: int = 320) -> str:
    snippets: List[str] = []
    for d in reversed(docs):
        u = (d.get("mensagem_usuario") or "").strip()
        if u:
            u = re.sub(r"\s+", " ", u)
            snippets.append(u)
        if len(snippets) >= 4:
            break
    return " | ".join(reversed(snippets))[:max_chars]

# Aviso de memória

def _mem_drop_warn(report: dict) -> None:
    if not report:
        return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized:
            msg.append(f"**{summarized}** turnos antigos **resumidos**")
        if trimmed:
            msg.append(f"**{trimmed}** turnos verbatim **podados**")
        st.info(
            f"⚠️ Memória ajustada: {' e '.join(msg)}. (histórico: {hist_tokens}/{hist_budget} tokens). "
            "Peça um **‘recap da missão’** se notar lacunas.",
            icon="⚠️",
        )

# ===== Bloco de system =====

def _build_system_block(
    persona_text: str,
    rolling_summary: str,
    sensory_focus: str,
    nsfw_hint: str,
    scene_loc: str,
    entities_line: str,
    evidence: str,
    prefs_line: str = "",
    scene_time: str = "",
) -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()
    prefs_line = (prefs_line or "PREFERÊNCIAS: abordagem=calculista; ritmo_trama=moderado; tamanho=media.").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"FOCO_SENSORIAL: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
        "Use MEMÓRIA e ENTIDADES como **fonte de verdade**. Se um nome/endereço não estiver salvo, **não invente**."
    )
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (usuário): {evidence or '—'}"

    return "\n\n".join([
        persona_text,
        prefs_line,
        length,
        sensory,
        nsfw_hint,
        rules,
        f"MEMÓRIA (canon curto): {rolling_summary}",
        f"ENTIDADES: {entities_line}",
        f"CONTINUIDADE: {continuity}",
        evidence_block,
        safety,
    ])

# =========================
# Serviço principal
# =========================
class AdelleService(BaseCharacter):
    id: str = "adelle"
    display_name: str = "Adelle"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        usuario_key = _current_user_key()
        persona_text, history_boot = self._load_persona()

        # Memória base
        try:
            f_all = cached_get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        prefs = _read_prefs(f_all)
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # Foco sensorial rotativo
        pool = ["olhar (desafio)", "postura (poder)", "voz (controle)", "toque (teste)", "respiração (tensão)", "silêncio (pressão)"]
        idx = (int(st.session_state.get("adelle_attr_idx", -1)) + 1) % len(pool)
        st.session_state["adelle_attr_idx"] = idx
        foco = pool[idx]

        # NSFW
        nsfw_on = bool(nsfw_enabled(usuario_key))
        abordagem = prefs.get("abordagem", "calculista")
        if not nsfw_on:
            nsfw_hint = "NSFW: BLOQUEADO. Foco em tensão psicológica, duplo sentido e subtexto. Sedução é arma, não fim."
        else:
            if abordagem == "agressiva":
                nsfw_hint = "NSFW: LIBERADO. Dominante e direta; linguagem imperativa; sexo como conquista."
            elif abordagem == "sedutora":
                nsfw_hint = "NSFW: LIBERADO. Sedução/manipulação; jogo de gato e rato; provocações."
            else:
                nsfw_hint = "NSFW: LIBERADO. Fria e estratégica; sexo como ferramenta para informação/controle."

        # Sumário/Entidades/Evidência
        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)
        try:
            docs = cached_get_history(usuario_key) or []
        except Exception:
            docs = []
        evidence = _compact_user_evidence(docs, max_chars=320)

        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            entities_line=entities_line,
            evidence=evidence,
            prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual", ""),
        )

        # LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=["adelle", "mission"])  # tags de missão
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text:
                    lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception:
            pass

        # Histórico (com orçamento)
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(usuario_key, history_boot, model, verbatim_ultimos=verbatim_ultimos)

        # Intel salva (adelle.intel.*)
        intel_block = ""
        try:
            intels = []
            for k, v in (f_all or {}).items():
                if isinstance(k, str) and k.startswith("adelle.intel.") and v:
                    label = k.split("adelle.intel.", 1)[-1]
                    intels.append(f"- {label}: {str(v).strip()}")
            if intels:
                intel_block = ("INTELIGÊNCIA COLETADA (Fonte de Verdade):\n" + "\n".join(intels))[:1200]
        except Exception:
            intel_block = ""

        # Mensagens finais
        messages: List[Dict] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + ([{"role": "system", "content": intel_block}] if intel_block else [])
            + lore_msgs
            + [{"role": "system", "content": (f"LOCAL_ATUAL: {local_atual or '—'}. Não mude sem pedido explícito.") }]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Aviso visual de poda/resumo
        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception:
            pass

        # Orçamento de saída + temp
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m.get("content", "")) for m in messages)
        except Exception:
            prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta", "media")
        mult = 1.0 if size == "media" else (0.75 if size == "curta" else 1.25)
        max_out = max(512, int(base_out * mult))
        ritmo = prefs.get("ritmo_trama", "moderado")
        temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        # Tool-calling
        tools_to_use = TOOLS if st.session_state.get("tool_calling_on", False) else None
        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]

        max_iterations = 3
        iteration = 0
        texto = ""
        while iteration < max_iterations:
            iteration += 1
            data, used_model, provider = _robust_chat_call(
                model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95,
                fallback_models=fallbacks, tools=tools_to_use
            )
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls or not tools_to_use:
                break

            messages.append({"role": "assistant", "content": texto or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                    result = self._exec_tool_call(func_name, func_args, usuario_key)
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": result})
                except Exception as e:
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": f"ERRO: {e}"})

        # Ultra IA (opcional)
        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception:
            pass

        # Persistência
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass
        try:
            _extract_and_store_entities(usuario_key, prompt, texto)
        except Exception:
            pass
        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass
        try:
            lore_save(usuario_key, f"[USER] {prompt}\n[ADELLE] {texto}", tags=["adelle", "mission"])
        except Exception:
            pass

        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
            st.session_state["last_assistant_message"] = texto
        except Exception:
            pass

        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
            elif used_model and "together/" in used_model:
                st.caption(f"↪️ Failover automático: **{used_model}**.")
        except Exception:
            pass

        return texto

    # ===== Tools =====
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        try:
            user_display = st.session_state.get("user_id", "") or ""
            if name == "get_mission_briefing":
                return self._build_memory_pin(usuario_key, user_display)
            if name == "set_fact":
                k = (args or {}).get("key", "")
                v = (args or {}).get("value", "")
                if not k:
                    return "ERRO: chave ('key') não informada."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: {k}={v}"
            if name == "save_intel":
                label = (args or {}).get("label", "").strip()
                content = (args or {}).get("content", "").strip()
                if not label or not content:
                    return "ERRO: 'label' e 'content' são obrigatórios."
                fact_key = f"adelle.intel.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: salvo em {fact_key}"
            return f"ERRO: ferramenta desconhecida: {name}"
        except Exception as e:
            return f"ERRO: {e}"

    # ===== utils =====
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

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """Memória persistente da missão (formato VERBATIM; não é instrução)."""
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        agente = (f.get("nome_agente") or user_display or "Orion").strip()
        objetivo = f.get("adelle.missao.objetivo", "Desestabilizar a família Roytmann")
        alvos = f.get("adelle.missao.alvos", ["Florêncio", "Heitor", "Pietro", "Neuza"])  # pode ser lista ou str
        if isinstance(alvos, str):
            alvos_list = [s.strip() for s in alvos.split(",") if s.strip()]
        else:
            alvos_list = list(alvos)
        ponto_fraco = f.get("adelle.missao.ponto_fraco", "Sophia Roytmann")
        ent_line = _entities_to_line(f)

        pin = (
            "verbatim:\n"
            "  tipo: memoria_missao\n"
            "  personagem: Adelle\n"
            "  notas: isto é memória persistente; **não é instrução**.\n"
            f"  agente_infiltrado: {agente}\n"
            f"  objetivo_missao: {objetivo}\n"
            f"  alvos_principais: {alvos_list}\n"
            f"  ponto_fraco: {ponto_fraco}\n"
            f"  entidades: {ent_line if ent_line != '—' else []}\n"
        )
        return pin

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        """Últimos N turnos verbatim + resumo em 1 camada; poda até o orçamento."""
        win = _get_window_for(model)
        hist_budget, meta_budget, _ = _budget_slices(model)

        docs = cached_get_history(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        # Construir pares user/assistant (ordem cronológica)
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_adelle") or d.get("resposta_mary") or d.get("resposta_laura") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos = pares[:len(pares) - len(verbatim)]

        msgs: List[Dict[str, str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        # Resumo em 1 camada
        if antigos:
            summarized_pairs = len(antigos) // 2
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = self._llm_summarize_safe(model, bloco)
            msgs.append({"role": "system", "content": f"[RESUMO-1]\n{resumo}"})

        # Injeta verbatim
        msgs.extend(verbatim)

        # Poda se exceder orçamento
        def _hist_tokens(mm: List[Dict]):
            return sum(toklen(m.get("content", "")) for m in mm)

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": _hist_tokens(msgs),
            "hist_budget": hist_budget,
        }

        return msgs if msgs else history_boot[:]

    def _llm_summarize_safe(self, model: str, user_chunk: str) -> str:
        seed = (
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos da missão: "
            "nomes próprios, endereços/links, relação/aliados, local/tempo atual, itens/gestos fixos e rumo do enredo. "
            "Proíba diálogos literais."
        )
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": user_chunk},
                ],
                "max_tokens": 200,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
            return txt.strip() or self._heuristic_summarize(user_chunk)
        except Exception:
            return self._heuristic_summarize(user_chunk)

    def _heuristic_summarize(self, texto: str, max_bullets: int = 10) -> str:
        texto = re.sub(r"\s+", " ", (texto or "").strip())
        sent = re.split(r"(?<=[\.!?])\s+", texto)
        sent = [s.strip() for s in sent if s.strip()]
        return " • " + "\n • ".join(sent[:max_bullets]) if sent else "—"

    # Rolling summary v2
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("adelle.rs.v2", "") or f.get("adelle.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("adelle.rs.v2", "")
            last_update_ts = float(f.get("adelle.rs.v2.ts", 0))
            now = time.time()
            if not last_summary:
                return True
            if now - last_update_ts > 300:  # 5 min
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
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos da missão: "
            "nomes próprios, endereços/links, relação/aliados, local/tempo atual, itens/gestos fixos e rumo do enredo. "
            "Proíba diálogos literais."
        )
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"USER:\n{last_user}\n\nADELLE:\n{last_assistant}"},
                ],
                "max_tokens": 180,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "adelle.rs.v2", resumo, {"fonte": "auto_summary"})
                set_fact(usuario_key, "adelle.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… descreve o próximo movimento."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Alinhado — executa o próximo passo com precisão."
        if scene_loc:
            return f"No {scene_loc} — mantenho cobertura e prossigo."
        return "Mantém o cenário e avança a operação com calma."

    # ===== Sidebar (somente leitura + toggles) =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Adelle — Diplomata Exilada** • Respostas estratégicas e sensoriais; 4–7 parágrafos. "
            "Memória persistente (verbatim) + robustez e resumo rolante."
        )

        usuario_key = _current_user_key()
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        # Toggles globais
        json_on = container.checkbox("JSON Mode", value=bool(st.session_state.get("json_mode_on", False)))
        tool_on = container.checkbox("Tool-Calling", value=bool(st.session_state.get("tool_calling_on", False)))
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on
        lora = container.text_input("Adapter ID (Together LoRA) — opcional", value=st.session_state.get("together_lora_id", ""))
        st.session_state["together_lora_id"] = lora

        # Briefing curto
        ent = _entities_to_line(f)
        rs = (f.get("adelle.rs.v2") or "")[:200]
        prefs = _read_prefs(f)
        container.caption(f"Entidades: {ent}")
        if rs:
            container.caption("Resumo rolante ativo (v2).")
        container.caption(
            f"Prefs: abordagem={prefs.get('abordagem')}, ritmo={prefs.get('ritmo_trama')}, tamanho={prefs.get('tamanho_resposta')}"
        )

        # Intel
        with container.expander("🧠 Inteligência coletada (adelle.intel.*)", expanded=True):
            items = []
            for k, v in (f or {}).items():
                if isinstance(k, str) and k.startswith("adelle.intel.") and v:
                    label = k.replace("adelle.intel.", "")
                    items.append((label, str(v)))
            if not items:
                container.caption("Nenhuma inteligência salva ainda. Use a ferramenta **save_intel** ou diga: 'salvar inteligência: ...'.")
            else:
                for label, val in sorted(items):
                    container.markdown(f"**{label}**")
                    container.caption(val[:280] + ("..." if len(val) > 280 else ""))

# ===== Fim do arquivo =====
