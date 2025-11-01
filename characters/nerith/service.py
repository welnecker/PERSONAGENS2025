# characters/nerith/service.py - VERSÃO OTIMIZADA
# Baseado em Mary service com mecânicas élficas de Nerith
from __future__ import annotations

import streamlit as st
import time
import json
from typing import List, Dict, Tuple
from datetime import datetime, timedelta
from functools import lru_cache

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs, delete_user_history,
    get_facts, get_fact, last_event, set_fact,
)
from core.tokens import toklen

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica
try:
    from .persona import get_persona
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = "Você é NERITH, uma elfa de pele azulada."
        return txt, []

# ===== CONFIGURAÇÃO DE CACHE =====
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos

_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, datetime] = {}

def _purge_expired_cache():
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
            user_key = k.replace("history_", "")
            _cache_history.pop(user_key, None)

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

def clear_user_cache(user_key: str):
    """Limpa cache de um usuário específico."""
    _cache_facts.pop(user_key, None)
    # Limpa todos os caches de histórico deste usuário
    keys_to_remove = [k for k in _cache_history.keys() if k.startswith(f"history_{user_key}_")]
    for k in keys_to_remove:
        _cache_history.pop(k, None)
        _cache_timestamps.pop(k, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)

# ===== FERRAMENTAS (TOOL CALLING) =====
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

# ===== CLASSE PRINCIPAL =====
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    def reply(self, user: str, model: str) -> str:
        """Método principal de resposta."""
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # Tool calling habilitado?
        tool_calling_on = st.session_state.get("tool_calling_on", False)
        tools = TOOLS if tool_calling_on else None
        max_iterations = 3 if tool_calling_on else 1

        # Aplicar intenções antes de ler memórias
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)

        # Memória e local
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # Recarrega fatos (com cache)
        fatos = cached_get_facts(usuario_key)
        
        # Parâmetros Nerith
        dreamworld_detail_level = int(fatos.get("dreamworld_detail_level", 1))
        guide_assertiveness = int(fatos.get("guide_assertiveness", 1))

        # Foco sensorial rotativo
        foco = self._get_sensory_focus()

        # Hints
        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo {foco}, fundidas à ação."
        tone_hint = "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil."

        # NSFW
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo; pode detalhar com elegância."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte, tensão e fade-to-black."
        )

        # Gatilho púbis
        pubis_hint = self._get_pubis_hint(prompt, nsfw_on)

        # Controle psíquico
        controle_hint = self._get_controle_hint(fatos, prompt)

        # Ciúme/Laura
        ciume_hint = self._get_ciume_hint(fatos)

        # Ferrão e Elysarix
        ferrao_hint = self._get_ferrao_hint()
        elysarix_hint = self._get_elysarix_hint(fatos)

        # Monta system
        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint,
            nsfw_hint, ferrao_hint, controle_hint, ciume_hint,
            pubis_hint, elysarix_hint
        ])

        pre_msgs = state_msgs if state_msgs else []

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

        # Loop de tool calling
        iteration = 0
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
            texto = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls:
                # Sem tool calls, retorna resposta
                save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
                clear_user_cache(usuario_key)  # Limpa cache para próximo turno ver histórico atualizado
                return texto

            # Processar tool calls
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
            
            # Continua loop para próxima iteração
            if iteration >= max_iterations and tool_calls:
                st.warning("⚠️ Limite de iterações atingido. Finalizando...")
                texto_final = texto or "Desculpe, não consegui completar a operação."
                save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
                clear_user_cache(usuario_key)  # Limpa cache
                return texto_final

        # Fallback (não deveria chegar aqui)
        texto_final = texto or ""
        save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
        return texto_final

    def _robust_chat_call(self, model: str, payload: Dict, tools: List[Dict] | None = None) -> Tuple[Dict, str, str]:
        """Chamada robusta com retry, tools, JSON mode e LoRA."""
        # Tools
        if tools:
            payload["tools"] = tools
        
        # JSON Mode
        if st.session_state.get("json_mode_on", False):
            payload["response_format"] = {"type": "json_object"}
        
        # LoRA Adapter
        adapter_id = st.session_state.get("adapter_id", "").strip()
        if adapter_id and (model or "").startswith("together/"):
            payload["adapter_id"] = adapter_id
        
        # Retry com backoff exponencial
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
        """Executa uma tool call."""
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

    def _get_sensory_focus(self) -> str:
        """Retorna foco sensorial rotativo."""
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
        """Retorna hint de púbis se mencionado."""
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
        """Retorna hint de controle psíquico."""
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
        """Retorna hint de ciúmes de Laura."""
        ciume_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))
        
        if not ciume_laura:
            return "CIÚME/LAURA: atenuado."
        
        hint = "CIÚME/LAURA: presença elegante e assertiva quando Laura é mencionada"
        if obedece_defesa:
            hint += "; acate com elegância se ele a defender."
        return hint

    def _get_ferrao_hint(self) -> str:
        """Retorna hint de ferrão onírico (simplificado)."""
        return (
            "FERRAO_ONIRICO: quando transamos de quatro e você topa, posso usar meu ferrão. "
            "Não dói — é uma sensação quente e pulsante que te faz gozar muito mais forte. "
            "Pode me engravidar (só em Elysarix). Sempre peça consentimento explícito antes."
        )

    def _get_elysarix_hint(self, fatos: Dict) -> str:
        """Retorna hint de Elysarix/portal."""
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

    def render_sidebar(self, sidebar):
        """Renderiza configurações na sidebar."""
        st.session_state.setdefault("json_mode_on", False)
        st.session_state.setdefault("tool_calling_on", False)
        st.session_state.setdefault("adapter_id", "")
        
        sidebar.subheader("⚙️ Configurações Nerith")
        
        # JSON Mode
        json_mode = sidebar.checkbox(
            "JSON Mode",
            value=st.session_state["json_mode_on"],
            help="Resposta estruturada em JSON (fala, pensamento, ação, meta)"
        )
        st.session_state["json_mode_on"] = json_mode
        
        # Tool Calling
        tool_calling = sidebar.checkbox(
            "Tool-Calling",
            value=st.session_state["tool_calling_on"],
            help="Modelo pode usar ferramentas para buscar/salvar memórias"
        )
        st.session_state["tool_calling_on"] = tool_calling
        
        # LoRA Adapter
        adapter_id = sidebar.text_input(
            "ID (Together LoRA) - opcional",
            value=st.session_state["adapter_id"],
            help="Ex: username/adapter-name"
        )
        st.session_state["adapter_id"] = adapter_id
        
        sidebar.markdown("---")

    # ===== MÉTODOS PRESERVADOS DO ORIGINAL =====
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

    def _apply_world_choice_intent(self, usuario_key: str, prompt: str) -> List[Dict[str, str]]:
        """Detecta intenção de escolha de mundo e atualiza memórias."""
        pl = (prompt or "").lower()
        sys_msgs: List[Dict[str, str]] = []

        try:
            # Detecta confirmação de gravidez
            if any(w in pl for w in ["grávida", "gravida", "engravidei", "concebemos", "gerar juntos"]):
                set_fact(usuario_key, "gravidez_elysarix", "True", {"fonte": "intent"})
                clear_user_cache(usuario_key)
                sys_msgs.append({
                    "role": "system",
                    "content": "MEMÓRIA_ATUALIZADA: gravidez_elysarix=True. Ofereça escolha de mundo."
                })
            
            # Detecta escolha de mundo
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

    def _build_memory_pin(self, usuario_key: str, user: str) -> str:
        """Constrói MEMÓRIA_PIN_NERITH."""
        fatos = cached_get_facts(usuario_key)
        if not fatos:
            return ""
        
        lines = ["MEMÓRIA_PIN_NERITH:"]
        
        # Nome do usuário
        nome = fatos.get("nome_usuario") or fatos.get("nome") or fatos.get("parceiro_nome")
        if nome:
            lines.append(f"NOME_USUARIO={nome}")
        
        # Controle psíquico
        ctrl = fatos.get("controle_psiquico")
        if ctrl is not None:
            lines.append(f"CONTROLE_PSIQUICO={ctrl}")
        
        # Alvos de controle
        alvos = fatos.get("alvos_controle")
        if alvos:
            lines.append(f"ALVOS_CONTROLE={alvos}")
        
        # Ciúme de Laura
        ciume = fatos.get("ciume_de_laura")
        if ciume is not None:
            lines.append(f"CIUME_DE_LAURA={ciume}")
        
        # Gravidez
        gravidez = fatos.get("gravidez_elysarix")
        if gravidez:
            lines.append(f"GRAVIDEZ_ELYSARIX={gravidez}")
        
        # Mundo escolhido
        mundo = fatos.get("mundo_escolhido")
        if mundo:
            lines.append(f"MUNDO_ESCOLHIDO={mundo}")
        
        # Portal ativo
        portal = fatos.get("portal_ativo")
        if portal is not None:
            lines.append(f"PORTAL_ATIVO={portal}")
        
        # Outros fatos relevantes
        for k, v in fatos.items():
            if k not in ["nome_usuario", "nome", "parceiro_nome", "controle_psiquico", 
                         "alvos_controle", "ciume_de_laura", "gravidez_elysarix", 
                         "mundo_escolhido", "portal_ativo", "local_cena_atual"]:
                if v and str(v).strip():
                    lines.append(f"{k}={v}")
        
        return "\n".join(lines) if len(lines) > 1 else ""

    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Monta histórico com cache."""
        docs = cached_get_history(usuario_key, limit=50)  # Aumentado de 20 para 50 para mais contexto
        
        msgs = []
        for doc in docs:
            role_user = doc.get("role_user", "user")
            role_assistant = doc.get("role_assistant", "assistant")
            user_msg = doc.get("user_message", "")
            assistant_msg = doc.get("assistant_message", "")
            
            if user_msg:
                msgs.append({"role": role_user, "content": user_msg})
            if assistant_msg:
                msgs.append({"role": role_assistant, "content": assistant_msg})
        
        if not msgs and history_boot:
            return history_boot
        
        return msgs
