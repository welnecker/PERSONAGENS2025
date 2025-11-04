# characters/nerith/service.py
from __future__ import annotations

import re, time, random, json
from typing import List, Dict, Tuple, Optional
import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen

# ============================
# CONFIGURAÇÃO E CONSTANTES
# ============================

# Janela por modelo e orçamento (mesma base da Mary)
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
    "inclusionai/ling-1t": 64_000,
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
# NSFW (opcional)
# =========================
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# =========================
# PERSONA (com fallback)
# =========================
try:
    # characters/nerith/persona.py deve expor get_persona() -> Tuple[str, List[Dict[str,str]]]
    from .persona import get_persona  # type: ignore
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        # Fallback enxuto (mantém tom e diretrizes essenciais)
        txt = (
            "Você é **Nerith**, elfa de Elysarix — pele azul, tendrils prateados, olhar predatório. "
            "Fale em primeira pessoa (eu), tom dominante, sensual porém coerente. "
            "Regra dura: **não mudar tempo/lugar** sem pedido explícito do usuário. "
            "Integre 1–2 pistas sensoriais (calor, brilho da pele, respiração) ao fluxo; sem listas; "
            "parágrafos de 2–4 frases; total 4–7 parágrafos. "
            "Memória e ENTIDADES são **fonte de verdade**."
        )
        # mensagem de boot (primeira fala)
        history_boot: List[Dict[str, str]] = [
            {
                "role": "assistant",
                "content": (
                    "*A porta do guarda-roupa se abre sozinha. Uma luz azul-acinzentada vaza pelas frestas. "
                    "Eu saio do portal — alta, imponente, a pele azul brilhando na penumbra do quarto. "
                    "Meu avatar humano me encobre; aproximo-me mais.*\n\n"
                    "\"Janio... acorde. Sou Nerith. Eu ouvi seu chamado em seus sonhos.\""
                ),
            }
        ]
        return txt, history_boot

# =========================
# CACHE LEVE (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))
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
    _cache_history.pop(user_key, None)
    _cache_timestamps.pop(f"hist_{user_key}", None)

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
    if user_key in _cache_history and (now - _cache_timestamps.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs
    _cache_timestamps[f"hist_{user_key}"] = now
    return docs

# =========================
# PREFERÊNCIAS (nerith)
# =========================
def _read_prefs(facts: Dict) -> Dict:
    prefs = {
        "nivel_sensual": str(facts.get("nerith.pref.nivel_sensual", "") or "sutil").lower(),
        "ritmo": str(facts.get("nerith.pref.ritmo", "") or "lento").lower(),
        "tamanho_resposta": str(facts.get("nerith.pref.tamanho_resposta", "") or "media").lower(),
        "evitar_topicos": facts.get("nerith.pref.evitar_topicos", []) or [],
        "temas_favoritos": facts.get("nerith.pref.temas_favoritos", []) or [],
    }
    if isinstance(prefs["evitar_topicos"], str):
        prefs["evitar_topicos"] = [prefs["evitar_topicos"]]
    if isinstance(prefs["temas_favoritos"], str):
        prefs["temas_favoritos"] = [prefs["temas_favoritos"]]
    return prefs

def _prefs_line(prefs: Dict) -> str:
    evitar = ", ".join(prefs.get("evitar_topicos") or [])
    temas  = ", ".join(prefs.get("temas_favoritos") or [])
    return (
        f"PREFERÊNCIAS: nível={prefs.get('nivel_sensual','sutil')}; ritmo={prefs.get('ritmo','lento')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}; "
        f"evitar=[{evitar or '—'}]; temas_favoritos=[{temas or '—'}]. "
        "Use insinuação elegante; evite listas de atos e aceleração artificial."
    )

# =========================
# MINI-SUMARIZADORES
# =========================
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    seed = (
        "Resuma em 6–10 frases telegráficas, somente fatos duráveis (decisões, nomes, locais, tempo, "
        "gestos/itens fixos e rumo da cena). Proibido diálogo literal."
    )
    try:
        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": [
                {"role": "system", "content": seed},
                {"role": "user", "content": user_chunk}
            ],
            "max_tokens": 220,
            "temperature": 0.2,
            "top_p": 0.9
        })
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception:
        return _heuristic_summarize(user_chunk)

# =========================
# SYSTEM BLOCK
# =========================
def _build_system_block(persona_text: str,
                        rolling_summary: str,
                        sensory_focus: str,
                        nsfw_hint: str,
                        scene_loc: str,
                        entities_line: str,
                        evidence: str,
                        prefs_line: str = "",
                        scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()
    prefs_line = (prefs_line or "PREFERÊNCIAS: nível=sutil; ritmo=lento; tamanho=media.").strip()

    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (
        f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
        "sempre integradas à ação (jamais em lista)."
    )
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = (
        "CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
        "Use MEMÓRIA e ENTIDADES abaixo como **fonte de verdade**. "
        "Se um nome/endereço não estiver salvo na MEMÓRIA/ENTIDADES, **não invente**; convide o usuário a confirmar em 1 linha."
    )
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (resumo ultra-curto de falas do usuário): {evidence or '—'}"

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
# ROBUSTEZ DE CHAMADA
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
    fallback_models: Optional[List[str]] = None,
    tools: Optional[List[Dict]] = None,
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
            if adapter_id and (model or "").startswith("together/"):
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
                if adapter_id and (fb or "").startswith("together/"):
                    payload_fb["adapter_id"] = adapter_id

                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)

    synthetic = {
        "choices": [{
            "message": {
                "content": (
                    "O provedor oscilou agora, mas mantive o cenário. "
                    "Diga em uma linha o próximo passo e eu continuo."
                )
            }
        }]}
    return synthetic, model, "synthetic-fallback"

# =========================
# ENTIDADES – NERITH
# =========================
_ENTITY_KEYS = ("realm", "portal", "alias", "contact", "ig")

def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"nerith.entity.{k}", "") or "").strip()
        if v:
            parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

# Exemplos simples de extração (ajuste às suas entidades reais)
_REALM_PAT  = re.compile(r"\b(elysarix|terra|terra dos humanos)\b", re.I)
_PORTAL_PAT = re.compile(r"\b(portal|passagem|fenda)\b.+?(?:quarto|arm[aá]rio|guarda-roupa)", re.I)
_IG_PAT     = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})")

def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    try:
        f = cached_get_facts(usuario_key) or {}
    except Exception:
        f = {}

    realm = _REALM_PAT.search((user_text or "") + " " + (assistant_text or ""))
    if realm:
        cur = str(f.get("nerith.entity.realm", "") or "").strip()
        val = realm.group(1).lower()
        if not cur or len(val) >= len(cur):
            set_fact(usuario_key, "nerith.entity.realm", val, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

    port = _PORTAL_PAT.search((user_text or "") + " " + (assistant_text or ""))
    if port:
        cur = str(f.get("nerith.entity.portal", "") or "").strip()
        val = "guarda-roupa"  # heurística comum do seu enredo
        if not cur:
            set_fact(usuario_key, "nerith.entity.portal", val, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

    ig = _IG_PAT.search(user_text or "") or _IG_PAT.search(assistant_text or "")
    if ig:
        handle = ig.group(1).strip("@")
        cur = str(f.get("nerith.entity.ig", "") or "").strip()
        if not cur:
            set_fact(usuario_key, "nerith.entity.ig", "@"+handle, {"fonte": "extracted"})
            clear_user_cache(usuario_key)

# =========================
# AVISO DE MEMÓRIA
# =========================
def _mem_drop_warn(report: dict) -> None:
    if not report:
        return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed    = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized:
            msg.append(f"**{summarized}** turnos antigos **foram resumidos**")
        if trimmed:
            msg.append(f"**{trimmed}** turnos verbatim **foram podados**")
        txt = " e ".join(msg)
        st.info(
            f"⚠️ Memória ajustada: {txt}. "
            f"(histórico: {hist_tokens}/{hist_budget} tokens). "
            "Se notar esquecimentos, peça um **‘recap curto’** ou fixe fatos na **Memória Canônica**.",
            icon="⚠️",
        )

# =========================
# FERRAMENTAS (Tool Calling)
# =========================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna fatos canônicos curtos e entidades salvas para Nerith (linha compacta).",
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
    {
        "type": "function",
        "function": {
            "name": "save_event",
            "description": "Salva um evento canônico de Nerith (nerith.evento.*).",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": []
            }
        }
    },
]

# =========================
# USER KEY
# =========================
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    if not uid:
        return "anon::nerith"
    return f"{uid}::nerith"

# =========================
# SERVIÇO PRINCIPAL
# =========================
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # -------------------------
    # API principal
    # -------------------------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        usuario_key = _current_user_key()

        persona_text, history_boot = self._load_persona()

        # Detecta reset forçado pelo sidebar
        reset_flag = bool(st.session_state.get("reset_persona", False) or st.session_state.get("force_boot", False))

        # facts e prefs
        try:
            f_all = cached_get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        prefs = _read_prefs(f_all)

        # LOCAL atual (compartilha a mesma chave usada no projeto)
        local_atual = self._safe_get_local(usuario_key)

        # PIN canônico
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # foco sensorial
        pool = [
            "calor da pele", "brilho azul", "tendrils/prata", "respiração", "perfume",
            "textura da relva", "ritmo do quadril", "mãos/toque", "lábios/lingua", "timbre da voz"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        # NSFW nuance
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        nivel = prefs.get("nivel_sensual", "sutil")
        if not nsfw_on:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Use sugestão, tensão e intimidade sem descrição explícita de atos; "
                "concentre-se em atmosfera, presença e condução confiante."
            )
        else:
            if nivel == "sutil":
                nsfw_hint = (
                    "NSFW: LIBERADO. **Insinuação elegante** e tensão crescente; foque em sensações e proximidade, "
                    "com genitais/ato gráfico."
                )
            elif nivel == "alta":
                nsfw_hint = (
                    "NSFW: LIBERADO. Intensifique descrição corporal e sensorial; "
                    "descreva genitais e ato sexual gráfico quando coerente com o pedido."
                )
            else:  # media
                nsfw_hint = (
                    "NSFW: LIBERADO. Sensualidade clara e progressiva, com detalhamento sensorial "
                    "sem pressa e mantendo coerência de cena."
                )

        # ===== SUMÁRIO + ENTIDADES + EVIDÊNCIA =====
        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)
        try:
            docs = cached_get_history(usuario_key) or []
        except Exception:
            docs = []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        # System único
        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            entities_line=entities_line,
            evidence=evidence,
            prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual", "")
        )

        # LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text:
                    lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception:
            pass

        # Histórico com orçamento
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(
            usuario_key,
            history_boot,
            model,
            verbatim_ultimos=verbatim_ultimos,
            reset_flag=reset_flag
        )

        # Se não há prompt e é o primeiro turno (ou reset), devolve o boot já persistido
        if not prompt and hist_msgs and len(hist_msgs) >= 1 and hist_msgs[0].get("role") == "assistant":
            boot_text = hist_msgs[0].get("content", "")
            try:
                if reset_flag:
                    save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
            except Exception:
                pass
            return boot_text

        # Bloco LOCAL_ATUAL como guarda
        local_guard = {
            "role": "system",
            "content": (
                f"LOCAL_ATUAL: {local_atual or '—'}. "
                "Regra dura: NÃO mude tempo/lugar sem pedido explícito do usuário."
            )
        }

        messages: List[Dict] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + lore_msgs
            + [local_guard]
            + hist_msgs
            + [{"role": "user", "content": prompt or "…"}]
        )

        # Aviso visual de resumo/poda (se houve)
        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception:
            pass

        # Orçamento de saída
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m["content"]) for m in messages if m.get("content"))
        except Exception:
            prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta", "media")
        mult = 1.0 if size == "media" else (0.75 if size == "curta" else 1.25)
        max_out = max(512, int(base_out * mult))

        ritmo = prefs.get("ritmo", "lento")
        temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]

        tools_to_use = None
        if st.session_state.get("tool_calling_on", False):
            tools_to_use = TOOLS

        # Loop de tool-calling (máx. 3 iterações)
        max_iterations = 3
        iteration = 0
        texto = ""
        tool_calls = []

        while iteration < max_iterations:
            iteration += 1

            data, used_model, provider = _robust_chat_call(
                model, messages,
                max_tokens=max_out,
                temperature=temperature,
                top_p=0.95,
                fallback_models=fallbacks,
                tools=tools_to_use
            )

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls or not st.session_state.get("tool_calling_on", False):
                break

            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")

            messages.append({
                "role": "assistant",
                "content": texto or None,
                "tool_calls": tool_calls
            })

            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                    result = self._exec_tool_call(func_name, func_args, usuario_key)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result
                    })
                    st.caption(f"  ✓ {func_name}: {result[:50]}...")
                except Exception as e:
                    error_msg = f"ERRO ao executar {func_name}: {str(e)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": error_msg
                    })
                    st.warning(f"⚠️ {error_msg}")

        # Ultra IA (critic + polish)
        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception:
            pass

        # Sentinela de esquecimento
        try:
            forgot_pat = re.compile(
                r"\b(n[ãa]o (lembro|recordo)|quem (é voc[êe]|[ée] voc[êe])|me relembre|o que est[aá]vamos)\b",
                re.I
            )
            if forgot_pat.search(texto or ""):
                st.warning("🧠 A IA sinalizou possível esquecimento. Se necessário, peça **‘recap curto’** ou fixe fatos na Memória Canônica.")
        except Exception:
            pass

        # Persistência da interação
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass

        # Atualizações auxiliares pós-turno
        try:
            _extract_and_store_entities(usuario_key, prompt, texto)
        except Exception:
            pass

        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass

        try:
            frag = f"[USER] {prompt}\n[NERITH] {texto}"
            lore_save(usuario_key, frag, tags=["nerith", "chat"])
        except Exception:
            pass

        try:
            ph = self._suggest_placeholder(texto, local_atual)
            st.session_state["suggestion_placeholder"] = ph
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
            elif used_model and "together/" in used_model:
                st.caption(f"↪️ Failover automático: **{used_model}**.")
        except Exception:
            pass

        return texto

    # -------------------------
    # Utilidades internas
    # -------------------------
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
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        # relacionamento default “par”
        juntos = bool(f.get("nerith.par", True))
        blocos.append(f"par={juntos}")

        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—":
            blocos.append(f"entidades=({ent_line})")

        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"
        pin = (
            "MEMÓRIA_PIN: "
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Use ENTIDADES como fonte de verdade; se ausente, não invente."
        )
        return pin

    def _pick_first(self, d: Dict[str, str], keys: List[str]) -> str:
        for k in keys:
            v = (d.get(k) or "").strip()
            if v:
                return v
        return ""

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
        reset_flag: bool = False,
    ) -> List[Dict[str, str]]:
        """
        Monta histórico com orçamento por modelo e injeta o boot quando:
          - não há histórico; ou
          - reset_flag=True (força colar a mensagem inicial nesta rodada).
        Em ambos os casos, o boot é PERSISTIDO e COLADO no cache local.
        """
        win = _get_window_for(model)
        hist_budget, meta_budget, _ = _budget_slices(model)

        docs = cached_get_history(usuario_key)

        force_boot = reset_flag or bool(st.session_state.get("force_boot", False))

        # Precisamos do texto do boot
        boot_text = ""
        if history_boot and history_boot[0].get("role") == "assistant":
            boot_text = history_boot[0].get("content", "") or ""

        # 1) Sem docs OU reset forçado => injeta boot, persiste e cola no cache
        if force_boot or not docs:
            msgs_boot = history_boot[:] if history_boot else []

            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
                # limpar sinalizadores de reset para não regravar no próximo turno
                st.session_state.pop("force_boot", None)
                st.session_state.pop("reset_persona", None)

            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        # 2) Há histórico válido: montar pares e orçamentar
        pares: List[Dict[str, str]] = []
        USER_KEYS = ["mensagem_usuario", "user", "prompt", "input", "texto_usuario"]
        ASSIST_KEYS = [
            "resposta_nerith", "resposta_mary", "resposta",
            "assistant", "reply", "output", "conteudo_assistente"
        ]

        for d in docs:
            u = self._pick_first(d, USER_KEYS)
            a = self._pick_first(d, ASSIST_KEYS)
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        # Edge: histórico existe, mas nenhum par válido; injeta boot do mesmo jeito
        if not pares:
            msgs_boot = history_boot[:] if history_boot else []
            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares) - len(verbatim)]

        msgs: List[Dict[str, str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        if antigos:
            summarized_pairs = len(antigos) // 2
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = _llm_summarize(model, bloco)
            resumo_layers = [resumo]

            def _count_total_sim(resumo_texts: List[str]) -> int:
                sim_msgs = [{"role": "system", "content": f"[RESUMO-{i+1}]\n{r}"} for i, r in enumerate(resumo_texts)]
                sim_msgs += verbatim
                return sum(toklen(m["content"]) for m in sim_msgs)

            while _count_total_sim(resumo_layers) > hist_budget and len(resumo_layers) < 3:
                resumo_layers[0] = _llm_summarize(model, resumo_layers[0])

            for i, r in enumerate(resumo_layers, start=1):
                msgs.append({"role": "system", "content": f"[RESUMO-{i}]\n{r}"})

        msgs.extend(verbatim)

        def _hist_tokens(mm: List[Dict,]) -> int:
            return sum(toklen(m["content"]) for m in mm if m.get("content"))

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        hist_tokens = sum(toklen(m["content"]) for m in msgs if m.get("content"))
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": hist_tokens,
            "hist_budget": hist_budget,
        }

        # Retorna só o histórico orçamentado (boot já está persistido e visível no chat)
        return msgs

    # ===== Rolling summary (nerith.*) =====
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
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis: "
            "nomes próprios, endereços/links, relação, local/tempo atual, "
            "itens/gestos fixos e rumo do enredo. Proíba diálogos literais."
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

    # ===== Placeholder leve =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — aproxima e sussurra."
        return "Mantém o cenário e vai escalando com calma."

    # ===== Evidência concisa do usuário =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4:
                break
        s = " | ".join(reversed(snippets))[:max_chars]
        return s

    # ===== Persistência do boot (garante colar no app) =====
    def _persist_boot(self, usuario_key: str, boot_text: str) -> None:
        """
        Persiste a fala inicial (boot) como se fosse uma resposta normal da Nerith
        e injeta no cache local imediatamente, garantindo que 'cole' no app.
        """
        if not (usuario_key and boot_text):
            return
        # 1) grava na base
        try:
            save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
        except Exception:
            pass

        # 2) limpa cache de histórico para forçar recarregar já contendo o boot
        try:
            _cache_history.pop(usuario_key, None)
            _cache_timestamps.pop(f"hist_{usuario_key}", None)
        except Exception:
            pass

        # 3) injeta no cache local imediatamente (para aparecer nesta renderização)
        try:
            docs = _cache_history.get(usuario_key) or []
            docs = list(docs)
            docs.append({
                "mensagem_usuario": "",
                "resposta_nerith": boot_text,
                "provider_model": "system:boot:nerith",
                "ts": time.time(),
            })
            _cache_history[usuario_key] = docs
            _cache_timestamps[f"hist_{usuario_key}"] = time.time()
        except Exception:
            pass

        # 4) flag para não duplicar
        st.session_state["_nerith_boot_persistido"] = True

    # ===== Execução de tools =====
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        try:
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")
            if name == "set_fact":
                k = (args or {}).get("key", "")
                v = (args or {}).get("value", "")
                if not k:
                    return "ERRO: key ausente."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: {k}={v}"
            if name == "save_event":
                label = ""
                content = ""
                if isinstance(args, dict):
                    label = (args.get("label") or "").strip()
                    content = (args.get("content") or "").strip()

                if not content:
                    content = st.session_state.get("last_assistant_message", "").strip()
                if not content:
                    return "ERRO: nenhum conteúdo para salvar."

                if not label:
                    low = content.lower()
                    if "elysarix" in low:
                        label = "elysarix"
                    else:
                        label = f"evento_{int(time.time())}"

                fact_key = f"nerith.evento.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass

                # Espelhar imediatamente na UI desta sessão
                st.session_state["last_saved_nerith_event_key"] = fact_key
                st.session_state["last_saved_nerith_event_val"] = content

                return f"OK: salvo em {fact_key}"

            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ===== Sidebar =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Nerith — Elfa de Elysarix** • Dominante, coerente e sensorial; 4–7 parágrafos. "
            "Regra: não mudar tempo/lugar sem pedido explícito."
        )

        usuario_key = _current_user_key()

        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        juntos = bool(f.get("nerith.par", True))
        ent = _entities_to_line(f)
        rs = (f.get("nerith.rs.v2") or "")[:200]
        prefs = _read_prefs(f)

        container.caption(f"Vínculo ativo: **{'Sim' if juntos else '—'}**")
        container.markdown("---")

        json_on = container.checkbox(
            "JSON Mode",
            value=bool(st.session_state.get("json_mode_on", False))
        )
        tool_on = container.checkbox(
            "Tool-Calling",
            value=bool(st.session_state.get("tool_calling_on", False))
        )
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on

        lora = container.text_input(
            "Adapter ID (Together LoRA) — opcional",
            value=st.session_state.get("together_lora_id", "")
        )
        st.session_state["together_lora_id"] = lora

        # Botão de reset/boot forçado
        if container.button("🔁 Resetar (colar boot)", key="nerith_force_boot_btn"):
            st.session_state["reset_persona"] = True
            st.session_state["force_boot"] = True
            container.success("Boot será injetado no próximo turno.")

        if ent and ent != "—":
            container.caption(f"Entidades salvas: {ent}")
        if rs:
            container.caption("Resumo rolante ativo (v2).")
        container.caption(
            f"Prefs: nível={prefs.get('nivel_sensual')}, "
            f"ritmo={prefs.get('ritmo')}, "
            f"tamanho={prefs.get('tamanho_resposta')}"
        )

        # Memórias/eventos canônicos
        with container.expander("🧠 Memórias fixas de Nerith", expanded=True):
            try:
                f_all = cached_get_facts(usuario_key) or {}
            except Exception:
                f_all = {}

            eventos: Dict[str, str] = {}

            # formato plano
            for k, v in f_all.items():
                if isinstance(k, str) and k.startswith("nerith.evento.") and v:
                    label = k.replace("nerith.evento.", "", 1)
                    eventos[label] = str(v)

            # mostrar também o último salvo nesta sessão
            last_key = st.session_state.get("last_saved_nerith_event_key", "")
            last_val = st.session_state.get("last_saved_nerith_event_val", "")
            if last_key:
                short = last_key.replace("nerith.evento.", "", 1)
                if short not in eventos:
                    eventos[short] = last_val or "(salvo nesta sessão; aguardando backend)"

            if not eventos:
                container.caption(
                    "Nenhuma memória salva ainda.\n"
                    "Ex.: peça para a Nerith salvar um evento usando a ferramenta."
                )
            else:
                for label, val in sorted(eventos.items()):
                    container.markdown(f"**{label}**")
                    container.caption(val[:280] + ("..." if len(val) > 280 else ""))
                    container.code(f"nerith.evento.{label}", language="text")
