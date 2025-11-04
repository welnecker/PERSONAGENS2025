# characters/nerith/service.py
# VERSÃO COMPACTA E ROBUSTA – pronta para colar
from __future__ import annotations

import re, time, random, json
from typing import List, Dict, Tuple, Optional
import streamlit as st

# ==== Núcleo do projeto (mantém seus imports originais) ====
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.repositories import (
    save_interaction, get_history_docs, get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen

# ==== Janela/Orçamento por modelo ====
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

# ==== NSFW (opcional) ====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# ==== PERSONA (com fallback seguro) ====
try:
    # characters/nerith/persona.py deve expor get_persona() -> Tuple[str, List[Dict[str,str]]]
    from .persona import get_persona  # type: ignore
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **Nerith**, elfa guerreira de Elysarix. "
            "Tom direto, presença física, proteção ativa e coerência de cena. "
            "Use MEMÓRIA/ENTIDADES como fonte de verdade; não mude tempo/lugar sem pedido."
        )
        history_boot = [{
            "role": "assistant",
            "content": "*A luz azul vaza do guarda-roupa. Eu piso no quarto, alta, pele azul em brilho baixo.* "
                       "\"Janio... acorde. Sou Nerith. Você me chamou.\""
        }]
        return txt, history_boot

# ==== CACHE LEVE ====
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))
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
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f; _cache_ts[f"facts_{user_key}"] = now
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
    _cache_history[user_key] = docs; _cache_ts[f"hist_{user_key}"] = now
    return docs

# ==== Preferências (opcional) ====
def _read_prefs(facts: Dict) -> Dict:
    prefs = {
        "nivel_sensual": str(facts.get("nerith.pref.nivel_sensual", "sutil")).lower(),
        "ritmo": str(facts.get("nerith.pref.ritmo", "lento")).lower(),
        "tamanho_resposta": str(facts.get("nerith.pref.tamanho_resposta", "media")).lower(),
        "evitar_topicos": facts.get("nerith.pref.evitar_topicos", []) or [],
        "temas_favoritos": facts.get("nerith.pref.temas_favoritos", []) or [],
    }
    if isinstance(prefs["evitar_topicos"], str): prefs["evitar_topicos"] = [prefs["evitar_topicos"]]
    if isinstance(prefs["temas_favoritos"], str): prefs["temas_favoritos"] = [prefs["temas_favoritos"]]
    return prefs

def _prefs_line(prefs: Dict) -> str:
    evitar = ", ".join(prefs.get("evitar_topicos") or [])
    temas  = ", ".join(prefs.get("temas_favoritos") or [])
    return (
        f"PREFERÊNCIAS: nível={prefs.get('nivel_sensual','sutil')}; ritmo={prefs.get('ritmo','lento')}; "
        f"tamanho={prefs.get('tamanho_resposta','media')}; evitar=[{evitar or '—'}]; "
        f"temas_favoritos=[{temas or '—'}]."
    )

# ==== Mini-sumários ====
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    seed = ("Resuma em 6–10 frases telegráficas; fatos duráveis (decisões, nomes, locais, relação/rumo). "
            "Proibido diálogo literal.")
    try:
        data, _, _ = route_chat_strict(model, {
            "model": model,
            "messages": [{"role": "system", "content": seed},
                         {"role": "user", "content": user_chunk}],
            "max_tokens": 220, "temperature": 0.2, "top_p": 0.9
        })
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception:
        return _heuristic_summarize(user_chunk)

# ==== System block ====
def _build_system_block(persona_text: str, rolling_summary: str, sensory_focus: str,
                        nsfw_hint: str, scene_loc: str, entities_line: str,
                        evidence: str, prefs_line: str = "", scene_time: str = "") -> str:
    persona_text = (persona_text or "").strip()
    rolling_summary = (rolling_summary or "—").strip()
    entities_line = (entities_line or "—").strip()
    prefs_line = (prefs_line or "PREFERÊNCIAS: nível=sutil; ritmo=lento; tamanho=media.").strip()
    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = (f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, "
               "sempre integradas à ação (jamais em lista).")
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; linguagem direta; sem metacena."
    rules = ("CONTINUIDADE: não mude tempo/lugar sem pedido explícito do usuário. "
             "Use MEMÓRIA/ENTIDADES como fonte de verdade. Se dado ausente, convide a confirmar em 1 linha.")
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (falas do usuário, ultra-curto): {evidence or '—'}"
    return "\n\n".join([
        persona_text, prefs_line, length, sensory, nsfw_hint, rules,
        f"MEMÓRIA (canon curto): {rolling_summary}",
        f"ENTIDADES: {entities_line}", f"CONTINUIDADE: {continuity}",
        evidence_block, safety
    ])

# ==== Robustez da chamada ====
def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text: return False
    s = err_text.lower()
    return ("cloudflare" in s) and any(code in s for code in ["500","502","503","504"])

def _robust_chat_call(
    model: str, messages: List[Dict[str, str]], *,
    max_tokens: int = 1536, temperature: float = 0.7, top_p: float = 0.95,
    fallback_models: Optional[List[str]] = None, tools: Optional[List[Dict]] = None
) -> Tuple[Dict, str, str]:
    attempts, last_err = 3, ""
    for i in range(attempts):
        try:
            payload = {
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": top_p
            }
            if tools: payload["tools"] = tools
            if st.session_state.get("json_mode_on", False):
                payload["response_format"] = {"type": "json_object"}
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or "").startswith("together/"):
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
                    "model": fb, "messages": messages, "max_tokens": max_tokens,
                    "temperature": temperature, "top_p": top_p
                }
                if tools: payload_fb["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload_fb["response_format"] = {"type": "json_object"}
                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or "").startswith("together/"):
                    payload_fb["adapter_id"] = adapter_id
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
    synthetic = {"choices":[{"message":{"content":
        "Conexão oscilou. Me diga em 1 linha o próximo passo e eu continuo do ponto exato."}}]}
    return synthetic, model, "synthetic-fallback"

# ==== ENTIDADES – linha compacta ====
_ENTITY_KEYS = ("realm","portal","alias","contact","ig")
def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"nerith.entity.{k}", "") or "").strip()
        if v: parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

# ==== Aviso visual de poda/resumo ====
def _mem_drop_warn(report: dict) -> None:
    if not report: return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed    = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized: msg.append(f"**{summarized}** turnos antigos **resumidos**")
        if trimmed:    msg.append(f"**{trimmed}** pares verbatim **podados**")
        txt = " e ".join(msg)
        st.info(f"⚠️ Memória ajustada: {txt}. (histórico: {hist_tokens}/{hist_budget} tokens).", icon="⚠️")

# ==== Tools (opcional) ====
TOOLS = [
    {"type": "function", "function": {
        "name": "get_memory_pin",
        "description": "Retorna fatos canônicos curtos e entidades salvas (linha compacta) para Nerith.",
        "parameters": {"type": "object","properties":{},"required":[]}
    }},
    {"type": "function", "function": {
        "name": "set_fact",
        "description": "Salva/atualiza um fato canônico (chave/valor) para Nerith.",
        "parameters": {"type":"object","properties":{
            "key":{"type":"string"},"value":{"type":"string"}}, "required":["key","value"]}
    }},
    {"type": "function", "function": {
        "name": "save_event",
        "description": "Salva um evento canônico de Nerith (nerith.evento.*).",
        "parameters": {"type":"object","properties":{
            "label":{"type":"string"},"content":{"type":"string"}}, "required":[]}
    }},
]

# ==== User key ====
def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid}::nerith" if uid else "anon::nerith"

# ============ SERVIÇO PRINCIPAL ============
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        usuario_key = _current_user_key()

        persona_text, history_boot = self._load_persona()

        # reset/colar boot via sidebar
        reset_flag = bool(st.session_state.get("reset_persona", False)
                          or st.session_state.get("force_boot", False))

        # facts/prefs
        try: f_all = cached_get_facts(usuario_key) or {}
        except Exception: f_all = {}
        prefs = _read_prefs(f_all)

        # local/momento
        local_atual = self._safe_get_local(usuario_key)

        # PIN canônico
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # foco sensorial (rotativo leve)
        pool = ["calor da pele","brilho azul","respiração","perfume","toque das mãos","timbre da voz"]
        idx = (int(st.session_state.get("nerith_attr_idx", -1)) + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        # NSFW nuance
        try: nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception: nsfw_on = False
        nivel = prefs.get("nivel_sensual","sutil")
        if not nsfw_on:
            nsfw_hint = ("NSFW: BLOQUEADO. Use sugestão e tensão sem descrição explícita; foque em atmosfera e condução.")
        else:
            nsfw_hint = {
                "sutil": "NSFW: LIBERADO. Insinuação elegante; foco sensorial; sem gráficos excessivos.",
                "alta":  "NSFW: LIBERADO. Intensidade elevada com detalhes sensoriais quando solicitado.",
            }.get(nivel, "NSFW: LIBERADO. Sensualidade progressiva com coerência de cena.")

        # resumo + entidades + evidência recente
        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)
        try: docs = cached_get_history(usuario_key) or []
        except Exception: docs = []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        # system único
        system_block = _build_system_block(
            persona_text=persona_text, rolling_summary=rolling, sensory_focus=foco,
            nsfw_hint=nsfw_hint, scene_loc=local_atual or "—", entities_line=entities_line,
            evidence=evidence, prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual","")
        )

        # LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto","") for d in top if d.get("texto"))
                if lore_text: lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception: pass

        # histórico com orçamento + boot
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(usuario_key, history_boot, model,
                                           verbatim_ultimos=verbatim_ultimos, reset_flag=reset_flag)

        # se for primeiro turno/reset e sem prompt → retorna boot
        if not prompt and hist_msgs and hist_msgs[0].get("role") == "assistant":
            boot_text = hist_msgs[0].get("content","")
            try:
                if reset_flag: save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
            except Exception: pass
            return boot_text

        # guarda de local (não mudar tempo/lugar)
        local_guard = {"role":"system","content":
                       f"LOCAL_ATUAL: {local_atual or '—'}. Regra: NÃO mude tempo/lugar sem pedido explícito."}

        messages: List[Dict] = (
            [{"role":"system","content": system_block}]
            + ([{"role":"system","content": memoria_pin}] if memoria_pin else [])
            + lore_msgs
            + [local_guard]
            + hist_msgs
            + [{"role":"user","content": prompt or "…"}]
        )

        # aviso de poda/resumo
        try: _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception: pass

        # orçamento saída
        win = _get_window_for(model)
        try: prompt_tokens = sum(toklen(m.get("content","")) for m in messages if m.get("content"))
        except Exception: prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta","media")
        mult = 1.0 if size=="media" else (0.75 if size=="curta" else 1.25)
        max_out = max(512, int(base_out * mult))
        temperature = 0.6 if prefs.get("ritmo","lento")=="lento" else (0.9 if prefs.get("ritmo")=="rapido" else 0.7)

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]
        tools_to_use = TOOLS if st.session_state.get("tool_calling_on", False) else None

        # loop de tool-calling (até 3)
        texto, tool_calls = "", []
        for iteration in range(1, 4):
            data, used_model, provider = _robust_chat_call(
                model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95,
                fallback_models=fallbacks, tools=tools_to_use
            )
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content","") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls or not tools_to_use:
                break

            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")
            messages.append({"role":"assistant","content": texto or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func = tc.get("function", {}) or {}
                fname = func.get("name",""); args_raw = func.get("arguments","{}")
                try:
                    args = json.loads(args_raw) if args_raw else {}
                    result = self._exec_tool_call(fname, args, usuario_key)
                    messages.append({"role":"tool","tool_call_id": tool_id, "content": result})
                    st.caption(f"  ✓ {fname}: {result[:50]}...")
                except Exception as e:
                    err = f"ERRO ao executar {fname}: {str(e)}"
                    messages.append({"role":"tool","tool_call_id": tool_id, "content": err})
                    st.warning(f"⚠️ {err}")

        # Ultra IA (opcional)
        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception: pass

        # sentinela de esquecimento
        try:
            if re.search(r"\b(n[ãa]o (lembro|recordo)|quem (é|[ée] voc[êe])|me relembre|o que est[aá]vamos)\b", texto, re.I):
                st.warning("🧠 Possível esquecimento detectado. Se quiser, peça um **recap curto**.")
        except Exception: pass

        # persistências
        try: save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception: pass

        # entidades e resumo rolante
        try: self._update_entities(usuario_key, prompt, texto)
        except Exception: pass
        try: self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception: pass

        # memória longa (lore)
        try:
            frag = f"[USER] {prompt}\n[NERITH] {texto}"
            lore_save(usuario_key, frag, tags=["nerith","chat"])
        except Exception: pass

        # placeholder leve
        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        # avisos de failover
        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
        except Exception: pass

        return texto

    # ===== Internals =====
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
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro: blocos.append(f"parceiro_atual={parceiro}")

        # vínculo padrão
        juntos = bool(f.get("nerith.par", True))
        blocos.append(f"par={juntos}")

        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—": blocos.append(f"entidades=({ent_line})")

        try: ev = last_event(usuario_key, "primeira_vez")
        except Exception: ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"
        return ("MEMÓRIA_PIN: "
                f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
                "Use ENTIDADES/MEMÓRIA como fonte de verdade; se faltar, não invente.")

    def _montar_historico(
        self, usuario_key: str, history_boot: List[Dict[str, str]], model: str,
        verbatim_ultimos: int = 10, reset_flag: bool = False,
    ) -> List[Dict[str, str]]:
        win = _get_window_for(model)
        hist_budget, _, _ = _budget_slices(model)
        docs = cached_get_history(usuario_key)
        force_boot = reset_flag or bool(st.session_state.get("force_boot", False))

        # texto do boot
        boot_text = ""
        if history_boot and history_boot[0].get("role") == "assistant":
            boot_text = history_boot[0].get("content","") or ""

        # sem docs OU reset forçado → injeta boot e persiste
        if force_boot or not docs:
            msgs_boot = history_boot[:] if history_boot else []
            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
                st.session_state.pop("force_boot", None)
                st.session_state.pop("reset_persona", None)
            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        # pares user/assistant
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_nerith") or d.get("resposta_mary") or "").strip()
            if u: pares.append({"role":"user","content":u})
            if a: pares.append({"role":"assistant","content":a})

        if not pares:
            msgs_boot = history_boot[:] if history_boot else []
            if boot_text and not st.session_state.get("_nerith_boot_persistido", False):
                self._persist_boot(usuario_key, boot_text)
            st.session_state["_mem_drop_report"] = {}
            return msgs_boot

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares)-len(verbatim)]

        msgs: List[Dict[str,str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        if antigos:
            summarized_pairs = len(antigos) // 2
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = _llm_summarize(model, bloco)
            resumo_layers = [resumo]

            def _count_total_sim(resumo_texts: List[str]) -> int:
                sim_msgs = [{"role":"system","content": f"[RESUMO-{i+1}]\n{r}"} for i, r in enumerate(resumo_texts)]
                sim_msgs += verbatim
                return sum(toklen(m["content"]) for m in sim_msgs)

            while _count_total_sim(resumo_layers) > hist_budget and len(resumo_layers) < 3:
                resumo_layers[0] = _llm_summarize(model, resumo_layers[0])

            for i, r in enumerate(resumo_layers, start=1):
                msgs.append({"role":"system","content": f"[RESUMO-{i}]\n{r}"})

        msgs.extend(verbatim)

        def _hist_tokens(mm: List[Dict]) -> int:
            return sum(toklen(m.get("content","")) for m in mm if m.get("content"))

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]; trimmed_pairs += 1
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        hist_tokens = sum(toklen(m.get("content","")) for m in msgs if m.get("content"))
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": hist_tokens,
            "hist_budget": hist_budget,
        }

        # injeta boot sempre no início
        return (history_boot[:] if history_boot else []) + msgs

    # ===== Rolling summary (nerith.rs.v2) =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("nerith.rs.v2", "") or f.get("nerith.rolling_summary","") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("nerith.rs.v2", "")
            last_update_ts = float(f.get("nerith.rs.v2.ts", 0))
            now = time.time()
            if not last_summary: return True
            if now - last_update_ts > 300: return True
            if (len(last_user) + len(last_assistant)) > 100: return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant): return
        seed = ("Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis "
                "(nomes, locais/tempo atual, relação/rumo, itens/gestos fixos). Proíba diálogos.")
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [{"role":"system","content": seed},
                             {"role":"user","content": f"USER:\n{last_user}\n\nNERITH:\n{last_assistant}"}],
                "max_tokens": 180, "temperature": 0.2, "top_p": 0.9
            })
            resumo = (data.get("choices",[{}])[0].get("message",{}) or {}).get("content","").strip()
            if resumo:
                set_fact(usuario_key, "nerith.rs.v2", resumo, {"fonte":"auto_summary"})
                set_fact(usuario_key, "nerith.rs.v2.ts", time.time(), {"fonte":"auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    # ===== Entidades básicas (heurística leve) =====
    _REALM_PAT  = re.compile(r"\b(elysarix|terra|mundo humano)\b", re.I)
    _PORTAL_PAT = re.compile(r"\b(portal|passagem|guarda-roupa)\b", re.I)
    _IG_PAT     = re.compile(r"(?:instagram\.com/|@)([A-Za-z0-9_.]{2,30})")

    def _update_entities(self, usuario_key: str, user_text: str, assistant_text: str) -> None:
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}

        realm = self._REALM_PAT.search((user_text or "") + " " + (assistant_text or ""))
        if realm:
            cur = str(f.get("nerith.entity.realm","") or "").strip()
            val = realm.group(1).lower()
            if not cur or len(val) >= len(cur):
                set_fact(usuario_key, "nerith.entity.realm", val, {"fonte":"extracted"}); clear_user_cache(usuario_key)

        if self._PORTAL_PAT.search((user_text or "") + " " + (assistant_text or "")):
            cur = str(f.get("nerith.entity.portal","") or "").strip()
            if not cur:
                set_fact(usuario_key, "nerith.entity.portal", "guarda-roupa", {"fonte":"extracted"}); clear_user_cache(usuario_key)

        ig = self._IG_PAT.search(user_text or "") or self._IG_PAT.search(assistant_text or "")
        if ig:
            handle = ig.group(1).strip("@")
            cur = str(f.get("nerith.entity.ig","") or "").strip()
            if not cur:
                set_fact(usuario_key, "nerith.entity.ig", "@"+handle, {"fonte":"extracted"}); clear_user_cache(usuario_key)

    # ===== Placeholder leve =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s: return "Continua do ponto exato… conduz."
        if any(k in s for k in ["vamos","topa","prefere","quer"]): return "Quero — mantém o cenário e conduz."
        if scene_loc: return f"No {scene_loc} mesmo — aproxima e conduz."
        return "Mantém o cenário e escala com calma."

    # ===== Evidência concisa =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4: break
        return (" | ".join(reversed(snippets)))[:max_chars]

    # ===== Persistência do boot (colar na hora) =====
    def _persist_boot(self, usuario_key: str, boot_text: str) -> None:
        if not (usuario_key and boot_text): return
        try: save_interaction(usuario_key, "", boot_text, "system:boot:nerith")
        except Exception: pass
        try:
            docs = _cache_history.get(usuario_key) or []
            docs = list(docs)
            docs.append({
                "mensagem_usuario": "", "resposta_nerith": boot_text,
                "provider_model": "system:boot:nerith", "ts": time.time(),
            })
            _cache_history[usuario_key] = docs; _cache_ts[f"hist_{usuario_key}"] = time.time()
        except Exception: pass
        st.session_state["_nerith_boot_persistido"] = True

    # ===== Execução de ferramentas =====
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        try:
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")
            if name == "set_fact":
                k = (args or {}).get("key",""); v = (args or {}).get("value","")
                if not k: return "ERRO: key ausente."
                set_fact(usuario_key, k, v, {"fonte":"tool_call"}); clear_user_cache(usuario_key)
                return f"OK: {k}={v}"
            if name == "save_event":
                label = (args or {}).get("label","").strip()
                content = (args or {}).get("content","").strip()
                if not content: content = st.session_state.get("last_assistant_message","").strip()
                if not content: return "ERRO: nenhum conteúdo para salvar."
                if not label:
                    low = content.lower()
                    label = "elysarix" if "elysarix" in low else f"evento_{int(time.time())}"
                fact_key = f"nerith.evento.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte":"tool_call"}); clear_user_cache(usuario_key)
                st.session_state["last_saved_nerith_event_key"] = fact_key
                st.session_state["last_saved_nerith_event_val"] = content
                return f"OK: salvo em {fact_key}"
            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ===== Sidebar =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Nerith — Elfa de Elysarix** • Direta, coerente e sensorial; 4–7 parágrafos. "
            "Regra: não mudar tempo/lugar sem pedido explícito."
        )

        usuario_key = _current_user_key()
        try: f = cached_get_facts(usuario_key) or {}
        except Exception: f = {}

        juntos = bool(f.get("nerith.par", True))
        ent = _entities_to_line(f)
        rs = (f.get("nerith.rs.v2") or "")[:200]
        prefs = _read_prefs(f)

        container.caption(f"Vínculo ativo: **{'Sim' if juntos else '—'}**")
        container.markdown("---")

        json_on = container.checkbox("JSON Mode", value=bool(st.session_state.get("json_mode_on", False)))
        tool_on = container.checkbox("Tool-Calling", value=bool(st.session_state.get("tool_calling_on", False)))
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on

        lora = container.text_input("Adapter ID (Together LoRA) — opcional",
                                    value=st.session_state.get("together_lora_id", ""))
        st.session_state["together_lora_id"] = lora

        if container.button("🔁 Resetar (colar boot)", key="nerith_force_boot_btn"):
            st.session_state["reset_persona"] = True
            st.session_state["force_boot"] = True
            container.success("Boot será injetado no próximo turno.")

        if ent and ent != "—": container.caption(f"Entidades: {ent}")
        if rs: container.caption("Resumo rolante ativo (v2).")
        container.caption(
            f"Prefs: nível={prefs.get('nivel_sensual')}, ritmo={prefs.get('ritmo')}, "
            f"tamanho={prefs.get('tamanho_resposta')}"
        )

        with container.expander("🧠 Memórias fixas de Nerith", expanded=True):
            try: f_all = cached_get_facts(usuario_key) or {}
            except Exception: f_all = {}
            eventos: Dict[str, str] = {}
            for k, v in f_all.items():
                if isinstance(k, str) and k.startswith("nerith.evento.") and v:
                    label = k.replace("nerith.evento.", "", 1)
                    eventos[label] = str(v)
            last_key = st.session_state.get("last_saved_nerith_event_key", "")
            last_val = st.session_state.get("last_saved_nerith_event_val", "")
            if last_key:
                short = last_key.replace("nerith.evento.", "", 1)
                if short not in eventos:
                    eventos[short] = last_val or "(salvo nesta sessão; aguardando backend)"
            if not eventos:
                container.caption("Nenhuma memória salva ainda. Peça para salvar um evento pela ferramenta.")
            else:
                for label, val in sorted(eventos.items()):
                    container.markdown(f"**{label}**")
                    container.caption(val[:280] + ("..." if len(val) > 280 else ""))
                    container.code(f"nerith.evento.{label}", language="text")
