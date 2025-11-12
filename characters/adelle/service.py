# characters/adelle/service.py
from __future__ import annotations

import re, time, random, json
from typing import List, Dict, Tuple
import streamlit as st

# ===== Base =====
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact
)
from core.tokens import toklen

# ===== LORE (opcional; tolerante à ausência) =====
try:
    from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
except Exception:
    def lore_topk(*_, **__): return []
    def lore_save(*_, **__): return None

# ===== Ultra IA (opcional; tolerante) =====
try:
    from core.ultra import critic_review, polish
except Exception:
    def critic_review(*_, **__): return ""
    def polish(*a, **k): return a[2] if len(a) >= 3 else ""

# ===== NSFW (opcional; seguro) =====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool: return False

# ===== Persona =====
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **Adelle Roitman — A Diplomata Exilada**. Falo em primeira pessoa. "
            "Tom: poder, controle e sedução estratégica. 4–7 parágrafos; 2–4 frases/parágrafo; "
            "sem listas e sem metacena. Respeite LOCAL_ATUAL."
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
            _cache_facts.pop(k, None); _cache_ts.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_ts.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None); _cache_ts.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None); _cache_ts.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None); _cache_ts.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_ts.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try: f = get_facts(user_key) or {}
    except Exception: f = {}
    _cache_facts[user_key] = f; _cache_ts[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_ts.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try: docs = get_history_docs(user_key) or []
    except Exception: docs = []
    _cache_history[user_key] = docs; _cache_ts[f"hist_{user_key}"] = now
    return docs

# =========================
# Robustez de chamada
# =========================
def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text: return False
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
            if tools: payload["tools"] = tools
            if st.session_state.get("json_mode_on", False):
                payload["response_format"] = {"type": "json_object"}
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or '').startswith('together/'):
                payload["adapter_id"] = adapter_id
            return route_chat_strict(model, payload)
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4)); continue
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
                if tools: payload_fb["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload_fb["response_format"] = {"type": "json_object"}
                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or '').startswith('together/'):
                    payload_fb["adapter_id"] = adapter_id
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
    synthetic = {"choices": [{"message": {"content":
        "O provedor oscilou agora, mas mantive o cenário. Diga em uma linha o que você quer e eu continuo."
    }}]}
    return synthetic, model, "synthetic-fallback"

# =========================
# Tool-Calling (Adelle)
# =========================
TOOLS = [
    {"type": "function", "function": {"name": "get_mission_briefing",
     "description": "Retorna o resumo/briefing da missão e fatos canônicos (linha compacta).",
     "parameters": {"type": "object","properties": {},"required": []}}},
    {"type": "function", "function": {"name": "set_fact",
     "description": "Salva/atualiza um fato canônico (chave/valor) da missão.",
     "parameters": {"type": "object","properties": {"key": {"type": "string"},"value": {"type": "string"}},
                    "required": ["key","value"]}}},
    {"type": "function", "function": {"name": "save_intel",
     "description": "Salva nova peça de inteligência (intel) rotulada.",
     "parameters": {"type": "object","properties": {"label": {"type": "string"},"content": {"type": "string"}},
                    "required": ["label","content"]}}}
]

# =========================
# Helpers de contexto/memória
# =========================
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid}::adelle" if uid else "anon::adelle"

# Preferências do usuário (estilo de missão)
def _read_prefs(facts: Dict) -> Dict:
    return {
        "abordagem": str(facts.get("adelle.pref.abordagem", "calculista")).lower(),
        "ritmo_trama": str(facts.get("adelle.pref.ritmo_trama", "moderado")).lower(),
        "tamanho_resposta": str(facts.get("adelle.pref.tamanho_resposta", "media")).lower(),
    }

def _prefs_line(prefs: Dict) -> str:
    return (
        f"PREFERÊNCIAS: abordagem={prefs.get('abordagem','calculista')}; "
        f"ritmo_trama={prefs.get('ritmo_trama','moderado')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}. Use tensão psicológica; evite romance clichê."
    )

# ENTIDADES/Intel
_ENTITY_KEYS = ("alvo_principal", "local_seguro", "contato_governo", "proximo_passo_missao")

def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"adelle.entity.{k}", "") or "").strip()
        if v: parts.append(f"{k}={v}")
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

def _compact_user_evidence(docs: List[Dict], max_chars: int = 320) -> str:
    snippets: List[str] = []
    for d in reversed(docs):
        u = (d.get("mensagem_usuario") or "").strip()
        if u:
            u = re.sub(r"\s+", " ", u)
            snippets.append(u)
        if len(snippets) >= 4: break
    return " | ".join(reversed(snippets))[:max_chars]

def _mem_drop_warn(report: dict) -> None:
    if not report: return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized: msg.append(f"**{summarized}** turnos antigos **resumidos**")
        if trimmed:    msg.append(f"**{trimmed}** turnos verbatim **podados**")
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
    persona_text   = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line  = (entities_line or "—").strip()
    prefs_line     = (prefs_line or "PREFERÊNCIAS: abordagem=calculista; ritmo_trama=moderado; tamanho=media.").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"FOCO_SENSORIAL: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito. "
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

        # Foco sensorial rotativo (Adelle — domínio e sutileza)
        pool = [
            "olhar (desafio)",
            "postura (poder)",
            "voz (controle)",
            "toque (teste)",
            "respiração (tensão)",
            "silêncio (pressão)"
        ]
        idx = (int(st.session_state.get("adelle_attr_idx", -1)) + 1) % len(pool)
        st.session_state["adelle_attr_idx"] = idx
        foco = pool[idx]

        # NSFW hint
        nsfw_on = bool(nsfw_enabled(usuario_key))
        if not nsfw_on:
            nsfw_hint = "NSFW: BLOQUEADO. Foco em tensão psicológica e subtexto. A sedução é uma arma, não um fim."
        else:
            nsfw_hint = "NSFW: LIBERADO. Descreva a tensão e o erotismo com maturidade quando solicitado pelo usuário."

        # Contexto de missão
        missao = f_all.get("missao", {}).get("objetivo", "Destruir a família Roytmann")
        alvo = f_all.get("entity", {}).get("alvo_principal", "Pietro Roytmann")
        contexto_missao = (
            f"MISSÃO ATUAL: {missao}. ALVO: {alvo}. "
            "Adelle atua com precisão, inteligência e manipulação emocional."
        )

        # Montagem do bloco de sistema
        system_block = "\n".join([
            persona_text,
            contexto_missao,
            f"LOCAL_ATUAL: {local_atual or '—'}",
            f"SENSORIAL_FOCO: {foco}.",
            nsfw_hint,
            "ESTILO: Narrativa cinematográfica em 4–7 parágrafos, cada um com 2–4 frases. "
            "Ritmo tenso e linguagem refinada, sem exageros literais ou metacena.",
            "CONTINUIDADE: Use MEMÓRIA e o histórico como fonte de verdade. "
            "Não repita falas; avance a cena mantendo coerência.",
            f"MEMÓRIA (verbatim):\n{memoria_pin or '—'}",
        ])

        # Histórico com resumo híbrido (mantém coerência)
        hist_msgs = self._montar_historico(
            usuario_key, history_boot, model,
            verbatim_ultimos=int(st.session_state.get("verbatim_ultimos", 10))
        )

        # Montagem final das mensagens
        messages = (
            [{"role": "system", "content": system_block}]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Cálculo de orçamento
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m.get("content", "")) for m in messages)
        except Exception:
            prompt_tokens = 0
        max_out = _safe_max_output(win, prompt_tokens)
        temperature = 0.7

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]

        data, used_model, provider = _robust_chat_call(
            model, messages, max_tokens=max_out,
            temperature=temperature, top_p=0.95, fallback_models=fallbacks
        )

        texto = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

        # ===== Persistência =====
        try:
            # Alguns repositórios aceitam kwargs com 'character', outros não.
            try:
                save_interaction(
                    usuario_key, prompt, texto,
                    f"{provider}:{used_model}", character="adelle"
                )
            except TypeError:
                # Assinatura antiga (4 args)
                save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass

        # Atualiza sessão e avisos
        try:
            st.session_state["last_assistant_message"] = texto
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
        except Exception:
            pass

        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
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
                if not k: return "ERRO: chave ('key') não informada."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try: clear_user_cache(usuario_key)
                except Exception: pass
                return f"OK: {k}={v}"
            if name == "save_intel":
                label = (args or {}).get("label", "").strip()
                content = (args or {}).get("content", "").strip()
                if not label or not content:
                    return "ERRO: 'label' e 'content' são obrigatórios."
                fact_key = f"adelle.intel.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
                try: clear_user_cache(usuario_key)
                except Exception: pass
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
        try: return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception: return ""

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}

        agente = (f.get("nome_agente") or user_display or "Orion").strip()
        objetivo = f.get("adelle.missao.objetivo", "Desestabilizar a família Roytmann")
        alvos = str(f.get("adelle.missao.alvos", "Florêncio, Heitor, Pietro, Neuza"))
        ponto_fraco = f.get("adelle.missao.ponto_fraco", "Sophia Roytmann")

        pin = (
            "**DOSSIÊ DA MISSÃO (MEMÓRIA CANÔNICA):**\n"
            f"- **Agente Infiltrado:** {agente} (codinome 'Orion').\n"
            f"- **Objetivo Principal:** {objetivo}.\n"
            f"- **Alvos Principais:** {alvos}.\n"
            f"- **Ponto Fraco Identificado:** {ponto_fraco}.\n"
            "---"
        )
        return pin

    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("adelle.rolling_summary", "") or "").strip()
        except Exception:
            return ""

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, user_prompt: str, assistant_response: str) -> None:
        try:
            current_summary = self._get_rolling_summary(usuario_key)
            update_prompt = f"""Você mantém um resumo conciso e atualizado de uma missão de espionagem.

SUMÁRIO ATUAL:
{current_summary if current_summary else "Nenhum sumário ainda."}

ÚLTIMA INTERAÇÃO:
USER: {user_prompt[:500]}
ADELLE: {assistant_response[:500]}

TAREFA: Atualize o sumário com 2–4 frases, focando fatos (alvos, locais, ações, decisões) e continuidade.
SUMÁRIO ATUALIZADO:"""
            messages = [{"role": "user", "content": update_prompt}]
            response, _, _ = _robust_chat_call(model=model, messages=messages, max_tokens=256, temperature=0.3, top_p=0.9)
            new_summary = ""
            if response and "choices" in response and response["choices"]:
                new_summary = response["choices"][0].get("message", {}).get("content", "").strip()
            if new_summary:
                set_fact(usuario_key, "adelle.rolling_summary", new_summary, {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

        def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        """
        Histórico híbrido: resumo do miolo antigo + últimos N turnos verbatim.
        Preenche st.session_state["_mem_drop_report"] para habilitar o banner ⚠️.
        """
        hist_budget, _, _ = _budget_slices(model)

        docs = cached_get_history(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        # 1) Constrói pares user/assistant a partir de múltiplas chaves
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (
                d.get("resposta_adelle")
                or d.get("resposta")             # genérico, se seu repo usar
                or d.get("assistant")            # fallback ultra-genérico
                or d.get("resposta_mary")
                or d.get("resposta_laura")
                or ""
            ).strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        # 2) Mantém últimos N turnos verbatim (N user+N assistant ≈ 2N mensagens)
        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos = pares[:-len(verbatim)] if keep else pares[:-0]

        # Função util para tokens
        def _tok(mm: List[Dict[str, str]]) -> int:
            try:
                return sum(toklen(m.get("content", "")) for m in mm)
            except Exception:
                return len("\n".join(m.get("content","") for m in mm)) // 4  # fallback tosco

        summarized_pairs = 0
        trimmed_pairs = 0
        msgs: List[Dict[str, str]] = []

        # 3) Se houver parte antiga, gera um RESUMO curto com o modelo
        resumo_txt = ""
        if antigos:
            try:
                resumo_prompt = (
                    "Resuma de forma concisa o diálogo anterior entre [USER] e [ADELLE], "
                    "mantendo apenas fatos de enredo (locais, objetivos, decisões, pistas, nomes). "
                    "Máximo 4 frases."
                )
                resumo_src = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in antigos[-24:])
                messages_sum = [
                    {"role": "system", "content": resumo_prompt},
                    {"role": "user", "content": resumo_src},
                ]
                resp, _, _ = _robust_chat_call(
                    model=model, messages=messages_sum, max_tokens=220, temperature=0.2, top_p=0.9
                )
                resumo_txt = (resp.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            except Exception:
                resumo_txt = ""

        if resumo_txt:
            msgs.append({"role": "system", "content": "[RESUMO HISTÓRICO]\n" + resumo_txt})
            summarized_pairs = max(1, len(antigos) // 2)  # número simbólico para o banner
        else:
            # se não conseguiu resumir, empilha um pouco de antigos
            msgs.extend(antigos[-6:])  # pequeno buffer de contexto

        # 4) Acrescenta os últimos turnos verbatim
        msgs.extend(verbatim)

        # 5) Poda se estourar o orçamento de histórico
        while _tok(msgs) > hist_budget and len(msgs) > 2:
            # remove do início (sem tocar no final verbatim)
            msgs.pop(0)
            trimmed_pairs += 1

        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": _tok(msgs),
            "hist_budget": hist_budget,
        }
        return msgs if msgs else history_boot[:]
