# main.py
from __future__ import annotations

import sys
import inspect
from pathlib import Path
import streamlit as st

# Garantir raiz no sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Imports defensivos
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    import traceback
    st.error(
        "Falha ao importar `characters.registry`.\n\n"
        f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"
    )
    st.stop()

# Provedores/modelos (lazy router)
try:
    from core.service_router import available_providers, list_models  # noqa: E402
except Exception as e:
    import traceback
    st.error(
        "Falha ao importar `core.service_router`.\n\n"
        f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"
    )
    st.stop()

st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")

# ===== Sidebar: Personagem =====
st.sidebar.title("Personagem")
char_names = list_characters()
default_idx = char_names.index("Mary") if "Mary" in char_names else 0
choice = st.sidebar.selectbox("Escolha", char_names, index=default_idx)

# Instancia service da personagem
service = get_service(choice)

# Sidebar específico (opcional por personagem)
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        import traceback
        st.sidebar.error(
            f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n"
            f"```\n{traceback.format_exc()}\n```"
        )
else:
    st.sidebar.caption("Sem preferências específicas para esta personagem.")

# ===== Sidebar: Provedor e Modelo =====
st.sidebar.markdown("---")
st.sidebar.subheader("Modelo")

providers = available_providers()
provider = st.sidebar.selectbox("Provedor", providers, index=0)

try:
    models = list_models(provider) or ["openrouter/auto"]
except Exception:
    models = ["openrouter/auto"]

model = st.sidebar.selectbox("Modelo", models, index=0)

# Hiperparâmetros básicos
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.6, 0.05)
top_p = st.sidebar.slider("Top-p", 0.0, 1.0, 0.9, 0.05)
max_tokens = st.sidebar.slider("Max tokens", 128, 4096, 1024, 64)

# ===== Corpo =====
st.title(service.title)
prompt = st.text_area("Você:", height=140, placeholder="Escreva sua fala/cena aqui…")
send = st.button("Enviar", type="primary")

def _call_reply_safe(svc, **params):
    """
    Chama svc.reply(...) passando apenas os parâmetros que a assinatura aceita.
    Também mapeia alguns aliases comuns: se o método aceitar 'text', preenche com prompt/user.
    """
    sig = inspect.signature(svc.reply)
    accepted = dict()

    # Alias úteis
    if "text" in sig.parameters and "text" not in params:
        params["text"] = params.get("prompt") or params.get("user") or ""

    for k, v in params.items():
        if k in sig.parameters:
            accepted[k] = v

    # Se nada bateu, tenta uma chamada posicional mínima com o prompt
    if not accepted:
        try:
            return svc.reply(params.get("prompt") or params.get("user") or "")
        except TypeError:
            # Se mesmo assim não deu, relança erro original
            pass

    return svc.reply(**accepted)

if send and (prompt or "").strip():
    with st.spinner("Gerando…"):
        try:
            # Parâmetros “largos”: passamos todos e deixamos o filtro decidir
            reply = _call_reply_safe(
                service,
                user=prompt,             # compat com serviços antigos
                prompt=prompt,           # compat com serviços novos
                model=model,
                provider=provider,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                user_id=st.session_state.get("user_id"),
            )
            st.markdown(reply)
        except Exception as e:
            import traceback
            st.error(
                f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n"
                f"```\n{traceback.format_exc()}\n```"
            )
