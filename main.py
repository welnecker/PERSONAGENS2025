# main.py
from __future__ import annotations

import sys
from pathlib import Path
import streamlit as st

# Garante raiz no sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import defensivo do registry
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    import traceback
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")
st.sidebar.title("Personagem")

# Lista e escolhe personagem
char_names = list_characters()
default_idx = char_names.index("Mary") if "Mary" in char_names else 0
choice = st.sidebar.selectbox("Escolha", char_names, index=default_idx)

# Instancia service
service = get_service(choice)

# Sidebar específico (defensivo)
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        import traceback
        st.sidebar.error(f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n"
                         f"```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências para esta personagem.")

# Corpo
st.title(service.title)

user = st.text_input("Você:", placeholder="Escreva sua fala/cena aqui…")

# Modelos disponíveis (defensivo)
get_models = getattr(service, "available_models", None)
models = ["gpt-5"]
if callable(get_models):
    try:
        models = get_models() or models
    except Exception:
        pass

model = st.selectbox("Modelo", models, index=0)

# Ação
if st.button("Enviar", type="primary") and user.strip():
    with st.spinner("Gerando…"):
        try:
            reply = service.reply(user=user, model=model)
            st.markdown(reply)
        except Exception as e:
            import traceback
            st.error(f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n"
                     f"```\n{traceback.format_exc()}\n```")
