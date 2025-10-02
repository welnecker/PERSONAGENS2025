# main.py
from __future__ import annotations

import os
import sys
import inspect
import traceback
from pathlib import Path
from typing import Optional, List, Tuple

import streamlit as st

# ============ Ajuste de path ============
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ============ Gate opcional por senha ============
import time, hmac, hashlib  # noqa: E402


def _check_scrypt(pwd: str) -> bool:
    cfg = st.secrets.get("auth", {})
    salt_hex = cfg.get("salt", "")
    hash_hex = cfg.get("password_scrypt", "")
    if not salt_hex or not hash_hex:
        return False
    try:
        calc = hashlib.scrypt(
            pwd.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14, r=8, p=1
        ).hex()
        return hmac.compare_digest(calc, hash_hex)
    except Exception:
        return False


def require_password_if_configured(app_name: str = "PERSONAGENS 2025"):
    if "auth" not in st.secrets:
        return  # sem configuração de auth → segue

    st.session_state.setdefault("_auth_ok", False)
    st.session_state.setdefault("_auth_attempts", 0)
    st.session_state.setdefault("_auth_block_until", 0.0)

    now = time.time()
    if now < st.session_state["_auth_block_until"]:
        wait = int(st.session_state["_auth_block_until"] - now)
        st.error(f"Tentativas excessivas. Tente novamente em {wait}s.")
        st.stop()

    if st.session_state["_auth_ok"]:
        with st.sidebar:
            if st.button("Sair"):
                for k in ["_auth_ok", "_auth_attempts", "_auth_block_until"]:
                    st.session_state.pop(k, None)
                st.rerun()
        return

    st.title(f"🔒 {app_name}")
    pwd = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if _check_scrypt(pwd):
            st.session_state["_auth_ok"] = True
            st.session_state["_auth_attempts"] = 0
            st.success("Acesso liberado.")
            st.rerun()
        else:
            st.session_state["_auth_attempts"] += 1
            backoff = 5 * (3 ** max(0, st.session_state["_auth_attempts"] - 1))
            st.session_state["_auth_block_until"] = time.time() + backoff
            st.error("Senha incorreta.")
            st.stop()

    st.stop()


# ============ Imports do app ============
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")
require_password_if_configured("PERSONAGENS 2025")

# Registry de personagens
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# Router de provedores/modelos (com fallback)
try:
    from core.service_router import available_providers, list_models  # noqa: E402
except Exception:
    def available_providers():
        provs = []
        if os.environ.get("OPENROUTER_API_KEY"):
            provs.append("openrouter")
        if os.environ.get("TOGETHER_API_KEY"):
            provs.append("together")
        return provs or ["mock"]

    def list_models(provider: str):
        if provider == "openrouter":
            return [
                "deepseek/deepseek-chat-v3-0324",
                "anthropic/claude-3.5-haiku",
                "qwen/qwen3-max",
                "nousresearch/hermes-3-llama-3.1-405b",
            ]
        if provider == "together":
            return [
                "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
                "together/Qwen/Qwen2.5-72B-Instruct",
                "together/Qwen/QwQ-32B",
            ]
        return ["mock/gpt-5-mini"]


# Repositório (status opcional)
try:
    from core.repositories import health as repo_health, get_fact, get_facts, get_history_docs, set_fact
    from core.repositories import register_event, list_events, delete_user_history, delete_last_interaction, delete_all_user_data
except Exception:
    def repo_health(): return {"backend": "unknown", "ok": False}
    def get_fact(_u, _k, default=None): return default
    def get_facts(_u): return {}
    def get_history_docs(_u, limit: int = 400): return []
    def set_fact(*_a, **_k): return None
    def register_event(*_a, **_k): return None
    def list_events(_u, limit=5): return []
    def delete_user_history(_u): return 0
    def delete_last_interaction(_u): return False
    def delete_all_user_data(_u): return {"hist": 0, "state": 0, "eventos": 0, "perfil": 0}

# NSFW toggle opcional
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool: return False

# Inferência de local (opcional)
try:
    from core.locations import infer_from_prompt as infer_location
except Exception:
    def infer_location(_prompt: str) -> Optional[str]: return None


# ============ Helpers ============
def _rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def _reload_history(user_key: str):
    st.session_state["history"] = []
    try:
        docs = get_history_docs(user_key)
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()
            if u:
                st.session_state["history"].append(("user", u))
            if a:
                st.session_state["history"].append(("assistant", a))
    except Exception as e:
        st.sidebar.warning(f"Não foi possível carregar o histórico: {e}")
    st.session_state["history_loaded_for"] = user_key


# ============ UI topo ============
st.title("PERSONAGENS 2025")

# Estado base
st.session_state.setdefault("user_name", "Janio Donisete")
st.session_state.setdefault("character", "Mary")
st.session_state.setdefault("auto_loc", True)
st.session_state.setdefault("history", [])               # type: List[Tuple[str, str]]
st.session_state.setdefault("history_loaded_for", None)

# Escolha do personagem
chars = list_characters()
if not chars:
    st.error("Nenhuma personagem encontrada em `characters/`.")
    st.stop()
default_idx = chars.index("Mary") if "Mary" in chars else 0

colA, colB = st.columns([2, 2])
with colA:
    user_name = st.text_input("👤 Usuário", value=st.session_state["user_name"])
with colB:
    character = st.selectbox("🎭 Personagem", chars, index=default_idx)

st.session_state["user_name"] = user_name
st.session_state["character"] = character

# Instância do service
try:
    service = get_service(character)
except Exception as e:
    st.error(f"Falha ao instanciar serviço da personagem **{character}**:\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# Sidebar específico da personagem (defensivo)
st.sidebar.title("Preferências")
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        st.sidebar.error(f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n"
                         f"```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências específicas para esta personagem.")

# Provedores e modelos
provs = available_providers()
if not provs:
    provs = ["mock"]

# Provider default conforme chaves
def _default_provider():
    if "openrouter" in provs and os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if "together" in provs and os.environ.get("TOGETHER_API_KEY"):
        return "together"
    return provs[0]


st.sidebar.markdown("---")
provider = st.sidebar.selectbox("Provedor", provs, index=provs.index(_default_provider()))
models = []
try:
    models = list_models(provider) or []
except Exception as e:
    st.sidebar.warning(f"Falha ao listar modelos de {provider}: {e}")

if not models:
    models = getattr(service, "available_models", lambda *_: ["mock/gpt-5-mini"])(provider) or ["mock/gpt-5-mini"]

model = st.sidebar.selectbox("🧠 Modelo", models, index=0)

# Status DB
st.sidebar.markdown("---")
st.sidebar.subheader("Banco de dados")
try:
    h = repo_health()
    label = f"{h.get('backend', 'unknown')} • {'OK' if h.get('ok') else 'ERRO'}"
    if h.get("backend") == "jsonkv" and h.get("path"):
        label += f" • {h['path']}"
    st.sidebar.caption(label)
except Exception as e:
    st.sidebar.caption(f"Status: erro ({e})")

# NSFW + Local atual
user_key = user_name if character.lower() == "mary" else f"{user_name}::{character.lower()}"
try:
    local_atual = get_fact(user_key, "local_cena_atual", "—")
except Exception:
    local_atual = "—"

nsfw_badge = "✅ NSFW ON" if nsfw_enabled(user_key) else "🔒 NSFW OFF"
st.sidebar.caption(f"Local atual: {local_atual}")
st.sidebar.caption(nsfw_badge)

# Auto inferir local
st.sidebar.markdown("---")
auto_loc = st.sidebar.checkbox(
    "📍 Inferir local automaticamente",
    value=st.session_state.get("auto_loc", True),
    help="Quando ligado, tenta detectar o lugar a partir da sua mensagem."
)
st.session_state["auto_loc"] = auto_loc

# Manutenção rápida
st.sidebar.markdown("---")
colX, colY = st.sidebar.columns(2)
if colX.button("🔄 Reset histórico"):
    try:
        delete_user_history(user_key)
        st.session_state["history"] = []
        st.session_state["history_loaded_for"] = None
        st.sidebar.success("Histórico apagado.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Falha: {e}")

if colY.button("⏪ Apagar último"):
    try:
        ok = delete_last_interaction(user_key)
        if ok:
            st.sidebar.info("Último turno apagado.")
            st.session_state["history_loaded_for"] = None
            st.rerun()
        else:
            st.sidebar.warning("Nada para apagar.")
    except Exception as e:
        st.sidebar.error(f"Falha: {e}")

if st.sidebar.button("🧨 Apagar tudo"):
    try:
        delete_all_user_data(user_key)
        st.sidebar.success("Tudo apagado para este usuário/personagem.")
        st.session_state["history"] = []
        st.session_state["history_loaded_for"] = None
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Falha: {e}")

# Carrega histórico se mudou de chave
if st.session_state["history_loaded_for"] != user_key:
    _reload_history(user_key)

# Render histórico
for role, content in st.session_state["history"]:
    if role == "user":
        with st.chat_message("user"):
            st.markdown(content)
    else:
        with st.chat_message("assistant", avatar="💚"):
            st.markdown(content)

# CONTINUAR + chat input
st.write("")
continuar_clicked = st.button(
    "▶️ Continuar",
    key="btn_continuar",
    help="Segue a cena de onde parou, no mesmo local."
)
user_prompt = st.chat_input(f"Envie sua mensagem para {character}")

prompt: Optional[str] = None
is_auto_continue = False
if continuar_clicked and not user_prompt:
    prompt = (
        "CONTINUAR: Prossiga a cena exatamente de onde a última resposta parou. "
        "Mantenha LOCAL_ATUAL, personagens presentes e tom. Não resuma; avance a ação e o diálogo em 1ª pessoa."
    )
    is_auto_continue = True
elif user_prompt:
    prompt = user_prompt

# Ciclo de geração
def _call_reply_safe():
    """Chama service.reply detectando parâmetros suportados."""
    params = {}
    sig = inspect.signature(service.reply)
    if "user" in sig.parameters:
        params["user"] = user_name
    if "prompt" in sig.parameters:
        params["prompt"] = prompt
    if "model" in sig.parameters:
        params["model"] = model
    if "provider" in sig.parameters:
        params["provider"] = provider
    # chaves opcionais úteis
    if "user_key" in sig.parameters:
        params["user_key"] = user_key
    return service.reply(**params)

if prompt:
    # exibe prompt do usuário/continuar
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if is_auto_continue else prompt)
    st.session_state["history"].append(("user", "🔁 Continuar" if is_auto_continue else prompt))

    # inferir local automaticamente apenas se veio texto do usuário
    if (not is_auto_continue) and st.session_state["auto_loc"]:
        try:
            loc = infer_location(prompt)
            if loc:
                set_fact(user_key, "local_cena_atual", loc, {"fonte": "ui/auto"})
        except Exception:
            pass

    with st.spinner("Gerando..."):
        try:
            reply = _call_reply_safe()
        except Exception as e:
            st.error(f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n"
                     f"```\n{traceback.format_exc()}\n```")
            reply = f"Erro ao gerar resposta: {e}"

    with st.chat_message("assistant", avatar="💚"):
        st.markdown(reply)
    st.session_state["history"].append(("assistant", reply))
