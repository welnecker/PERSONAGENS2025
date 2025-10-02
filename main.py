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


# ============ Config página ============
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")
require_password_if_configured("PERSONAGENS 2025")
st.title("PERSONAGENS 2025")

# ============ Imports do app ============
# Registry de personagens
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# Router de provedores/modelos
try:
    from core.service_router import available_providers, list_models, route_chat_strict  # noqa: E402
except Exception:
    # Fallback mínimo se service_router falhar
    def available_providers():
        provs = []
        if os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY"):
            provs.append("openrouter")
        if os.getenv("TOGETHER_API_KEY") or st.secrets.get("TOGETHER_API_KEY"):
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

# DB backend (para diagnóstico)
DB_BACKEND = "unknown"
try:
    from core.database import get_col  # noqa: E402
    # tenta expor BACKEND se definido no __init__.py do pacote
    try:
        from core.database import BACKEND as DB_BACKEND  # type: ignore
    except Exception:
        DB_BACKEND = os.getenv("DB_BACKEND", "unknown")
except Exception:
    get_col = None  # type: ignore

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled  # noqa: E402
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False


# ============ Estado base ============
st.session_state.setdefault("user_id", "Janio Donisete")
st.session_state.setdefault("character", "Mary")
st.session_state.setdefault("provider", None)
st.session_state.setdefault("model", None)
st.session_state.setdefault("history", [])  # type: List[Tuple[str, str]]

# ============ Sidebar ============
st.sidebar.title("Configuração")

# Personagem
try:
    chars = list_characters() or ["Mary", "Laura", "Nerith"]
except Exception:
    chars = ["Mary", "Laura", "Nerith"]
if st.session_state["character"] not in chars:
    st.session_state["character"] = chars[0]

character = st.sidebar.selectbox("Personagem", chars, index=chars.index(st.session_state["character"]))
st.session_state["character"] = character

# Provedor
providers = available_providers()
if not providers:
    providers = ["mock"]
if st.session_state["provider"] not in providers:
        # preferir openrouter, depois together
    default_provider = "openrouter" if "openrouter" in providers else providers[0]
    st.session_state["provider"] = default_provider

provider = st.sidebar.selectbox("Provedor", providers, index=providers.index(st.session_state["provider"]))
st.session_state["provider"] = provider

# Modelos
models = list_models(provider) or ["mock/gpt-5-mini"]
if (st.session_state["model"] not in models):
    st.session_state["model"] = models[0]

model = st.sidebar.selectbox("Modelo", models, index=models.index(st.session_state["model"]))
st.session_state["model"] = model

# Diagnóstico de chaves
or_ok = bool(os.getenv("OPENROUTER_API_KEY") or st.secrets.get("OPENROUTER_API_KEY"))
tg_ok = bool(os.getenv("TOGETHER_API_KEY") or st.secrets.get("TOGETHER_API_KEY"))
st.sidebar.markdown("### Diagnóstico")
st.sidebar.caption(f"OpenRouter: {'OK' if or_ok else '—'} | Together: {'OK' if tg_ok else '—'}")
st.sidebar.caption(f"DB backend: **{DB_BACKEND or 'unknown'}**")

# Ping DB
def _db_ping() -> str:
    if not get_col:
        return "get_col indisponível (core.database não importado)."
    try:
        col = get_col("healthcheck")
        col.insert_one({"k": "ping", "ts": time.time()})
        doc = col.find_one({"k": "ping"}, sort=[("ts", -1)])
        return "OK" if doc else "falhou"
    except Exception as e:
        return f"erro: {e}"

if st.sidebar.button("🔎 Testar DB"):
    st.sidebar.info(f"DB ping: {_db_ping()}")

# Ping provedor/modelo
def _llm_ping() -> str:
    try:
        payload = {
            "model": st.session_state["model"],
            "messages": [
                {"role": "system", "content": "Você é um sistema de saúde do app."},
                {"role": "user", "content": "Responda exatamente: PONG."}
            ],
            "max_tokens": 8,
            "temperature": 0.0,
        }
        data, used, prov = route_chat_strict(st.session_state["model"], payload, provider=st.session_state["provider"])
        # extrai texto de forma robusta
        txt = ""
        try:
            txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
            if not txt:
                txt = data.get("choices", [{}])[0].get("text", "") or ""
        except Exception:
            pass
        if "PONG" in txt.upper():
            return f"OK ({prov}:{used})"
        return f"resposta inesperada ({prov}:{used}): {txt[:80]!r}"
    except Exception as e:
        return f"erro: {e}"

if st.sidebar.button("🛰️ Ping modelo"):
    st.sidebar.info(f"LLM ping: {_llm_ping()}")

st.sidebar.markdown("---")

# ============ Instancia service da personagem ============
service = get_service(character)

# Sidebar específico do serviço (defensivo)
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        st.sidebar.error(f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n"
                         f"```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências específicas.")

# Badge NSFW e local atual
try:
    # Mary usa chave simples; outras isolam por personagem
    user_key = st.session_state["user_id"] if character == "Mary" else f"{st.session_state['user_id']}::{character.lower()}"
    nsfw_badge = "✅ NSFW ON" if nsfw_enabled(user_key) else "🔒 NSFW OFF"
except Exception:
    nsfw_badge = "?"

st.sidebar.markdown(f"**NSFW:** {nsfw_badge}")
st.sidebar.caption(f"Provedor atual: **{provider}**")
st.sidebar.caption(f"Modelo atual: **{model}**")

# ============ Histórico simples ============
for role, content in st.session_state["history"]:
    if role == "user":
        with st.chat_message("user"):
            st.markdown(content)
    else:
        with st.chat_message("assistant", avatar="💚"):
            st.markdown(content)

# ============ Entrada ============
with st.container():
    c1, c2 = st.columns([4, 1])
    with c1:
        prompt = st.chat_input(f"Mensagem para {character}")
    with c2:
        cont = st.button("▶️ Continuar")

final_prompt: Optional[str] = None
if cont and not prompt:
    final_prompt = (
        "CONTINUAR: Prossiga a cena exatamente de onde a última resposta parou. "
        "Mantenha LOCAL_ATUAL, personagens presentes e tom. Não resuma; avance a ação e o diálogo em 1ª pessoa."
    )
elif prompt:
    final_prompt = prompt

# ============ Geração ============
def _call_reply(_service, *, prompt_text: str) -> str:
    """Chama reply com introspecção para aceitar assinaturas diferentes."""
    sig = inspect.signature(_service.reply)
    kwargs = {}
    if "user" in sig.parameters:
        kwargs["user"] = st.session_state["user_id"]
    if "model" in sig.parameters:
        kwargs["model"] = st.session_state["model"]
    if "provider" in sig.parameters:
        kwargs["provider"] = st.session_state["provider"]
    if "prompt" in sig.parameters:
        kwargs["prompt"] = prompt_text
    if "text" in sig.parameters and "prompt" not in sig.parameters:
        kwargs["text"] = prompt_text  # compatibilidade com serviços antigos
    return _service.reply(**kwargs)

if final_prompt:
    # eco da mensagem do usuário
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if (cont and not prompt) else final_prompt)
    st.session_state["history"].append(("user", "🔁 Continuar" if (cont and not prompt) else final_prompt))

    with st.spinner("Gerando…"):
        try:
            reply = _call_reply(service, prompt_text=final_prompt)
        except Exception as e:
            reply = f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"

    with st.chat_message("assistant", avatar="💚"):
        st.markdown(reply)
    st.session_state["history"].append(("assistant", reply))
