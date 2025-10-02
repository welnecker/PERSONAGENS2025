# main.py
from __future__ import annotations

import os
import sys
import time
import hmac
import hashlib
import traceback
from pathlib import Path
from typing import Optional, List, Tuple

import streamlit as st

# ============ Ajuste de path ============
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============ Gate opcional por senha ============
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
    # Só ativa se houver bloco "auth" em secrets
    if "auth" not in st.secrets:
        return

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


# ============ Config inicial ============
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")
require_password_if_configured("PERSONAGENS 2025")


# ============ Registry de personagens ============
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()


# ============ Router de provedores/modelos ============
try:
    from core.service_router import available_providers as _avail_providers, list_models as _list_models  # noqa: E402
except Exception:
    _avail_providers = None
    _list_models = None


def _available_providers_norm() -> List[Tuple[str, bool, str]]:
    """
    Normaliza para lista de tuplas (nome, habilitado, detalhe).
    Aceita retorno oficial (lista de tuplas) ou fallback (lista de strings).
    """
    if callable(_avail_providers):
        try:
            provs = _avail_providers() or []
        except Exception:
            provs = []
    else:
        provs = []
        if os.environ.get("OPENROUTER_API_KEY"):
            provs.append(("OpenRouter", True, "OK"))
        if os.environ.get("TOGETHER_API_KEY"):
            provs.append(("Together", True, "OK"))

    norm: List[Tuple[str, bool, str]] = []
    for p in provs:
        if isinstance(p, (list, tuple)):
            name = str(p[0])
            enabled = bool(p[1]) if len(p) >= 2 else True
            detail = str(p[2]) if len(p) >= 3 else ""
            norm.append((name, enabled, detail))
        else:
            norm.append((str(p), True, ""))
    if not norm:
        norm = [("OpenRouter", False, "sem chave"), ("Together", False, "sem chave")]
    return norm


def _list_models_norm(provider: str) -> List[str]:
    if callable(_list_models):
        try:
            return _list_models(provider) or []
        except Exception:
            pass
    # Fallback
    if provider == "OpenRouter":
        return [
            "deepseek/deepseek-chat-v3-0324",
            "anthropic/claude-3.5-haiku",
            "qwen/qwen3-max",
            "nousresearch/hermes-3-llama-3.1-405b",
        ]
    if provider == "Together":
        return [
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/Qwen/QwQ-32B",
        ]
    return ["mock/gpt-5-mini"]


# ============ Estado base ============
st.session_state.setdefault("user_id", "Visitante")
st.session_state.setdefault("history", [])  # [(role, text)]
st.session_state.setdefault("character", "Mary")
st.session_state.setdefault("provider", None)
st.session_state.setdefault("model", None)

# ============ Sidebar: Personagem ============
st.sidebar.title("Personagem")
characters = list_characters()
default_idx = characters.index("Mary") if "Mary" in characters else 0
choice = st.sidebar.selectbox("Escolha", characters, index=default_idx)
service = get_service(choice)

# Render do sidebar específico da personagem
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        st.sidebar.error(f"Erro no sidebar de {getattr(service, 'title', choice)}:\n\n"
                         f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências específicas para esta personagem.")

st.sidebar.markdown("---")

# ============ Sidebar: Provedor / Modelo ============
provs = _available_providers_norm()
prov_labels = [f"{name} {'✅' if ok else '⛔'}" + (f" · {detail}" if detail else "")
               for (name, ok, detail) in provs]

default_provider_idx = 0
for i, (_, ok, _) in enumerate(provs):
    if ok:
        default_provider_idx = i
        break

prov_choice_idx = st.sidebar.selectbox(
    "Provedor",
    list(range(len(provs))),
    format_func=lambda i: prov_labels[i],
    index=default_provider_idx
)
provider = provs[prov_choice_idx][0]

models = _list_models_norm(provider)
if not models:
    models = ["mock/gpt-5-mini"]
model = st.sidebar.selectbox("Modelo", models, index=0)

# Persistir no estado
st.session_state["character"] = choice
st.session_state["provider"] = provider
st.session_state["model"] = model

# ============ Topo / Identidade ============
st.title(getattr(service, "title", choice))
user_id = st.text_input("👤 Usuário", value=st.session_state["user_id"])
st.session_state["user_id"] = user_id.strip() or "Visitante"

# ============ Histórico ============
for role, content in st.session_state["history"]:
    with st.chat_message("user" if role == "user" else "assistant", avatar=("💬" if role == "user" else "💚")):
        st.markdown(content)

# Campo do usuário e botão Continuar
colA, colB = st.columns([4, 1])
with colA:
    user_prompt = st.chat_input(f"Fale com {choice}")
with colB:
    continuar = st.button("▶️ Continuar", use_container_width=True)

prompt: Optional[str] = None
if continuar and not user_prompt:
    prompt = (
        "CONTINUAR: Prossiga a cena exatamente de onde parou. "
        "Mantenha LOCAL_ATUAL e o tom. Avance ação e diálogo em 1ª pessoa."
    )
elif user_prompt:
    prompt = user_prompt


# ============ Compat: chamar reply() em qualquer formato ============
def _call_reply_any(service_obj, *, user: str, model: str, prompt: str) -> str:
    """
    Tenta várias assinaturas de reply():
      1) reply(user=..., model=..., prompt=...)
      2) reply(user=..., model=..., text=...)
      3) reply(user=..., model=..., message=...)
      4) (atributo) service.user_prompt = prompt; reply(user=..., model=...)
      5) fallback posicional
    """
    tried = []

    # 1) nova assinatura
    try:
        return service_obj.reply(user=user, model=model, prompt=prompt)
    except TypeError as e:
        tried.append(f"prompt kw: {e}")

    # 2) variantes de nome
    for alt_kw in ("text", "message", "input_text"):
        try:
            return service_obj.reply(user=user, model=model, **{alt_kw: prompt})
        except TypeError as e:
            tried.append(f"{alt_kw} kw: {e}")

    # 3) atributo para serviços legados
    try:
        setattr(service_obj, "user_prompt", prompt)
        return service_obj.reply(user=user, model=model)
    except TypeError as e:
        tried.append(f"attr+2args: {e}")

    # 4) fallback posicional
    try:
        return service_obj.reply(user, model, prompt)  # type: ignore[misc]
    except TypeError as e:
        tried.append(f"pos3: {e}")
    try:
        return service_obj.reply(user, model)  # type: ignore[misc]
    except Exception as e:
        tried.append(f"pos2: {e}")

    raise TypeError("Formato de reply() incompatível. Tentativas: " + " | ".join(tried))


# ============ Geração ============
if prompt:
    st.session_state["history"].append(("user", "🔁 Continuar" if (continuar and not user_prompt) else prompt))
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if (continuar and not user_prompt) else prompt)

    with st.spinner("Gerando…"):
        try:
            reply = _call_reply_any(
                service,
                user=st.session_state["user_id"],
                model=st.session_state["model"],
                prompt=prompt,
            )
        except Exception as e:
            reply = f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"

    with st.chat_message("assistant", avatar="💚"):
        st.markdown(reply)
    st.session_state["history"].append(("assistant", reply))


# ============ Rodapé / Diagnóstico ============
with st.expander("⚙️ Diagnóstico rápido"):
    st.write("**Provedores detectados:**")
    for name, ok, detail in provs:
        st.write(f"- {name}: {'OK' if ok else 'NÃO CONFIGURADO'} {f'({detail})' if detail else ''}")
    st.write("**Modelo atual:**", model)
    st.write("**Personagem:**", choice)
    st.write("**Usuário:**", st.session_state["user_id"])
