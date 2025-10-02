# app/main.py
from __future__ import annotations
import os
import sys
import streamlit as st

# Garante que o diretório raiz esteja no PYTHONPATH
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from characters.registry import get_service, list_characters  # noqa: E402


def main():
    st.set_page_config(page_title="PERSONAGENS2025", page_icon="💚", layout="centered")

    # Sidebar — seleção de personagem
    personagens = list_characters()
    if not personagens:
        st.error("Nenhuma personagem registrada em characters/registry.py")
        return

    char = st.sidebar.selectbox("Personagem", personagens, index=0)
    service = get_service(char)

    # Modelo (padrão do serviço, se houver)
    default_model = getattr(service, "default_model", "gpt-4o-mini")
    model = st.sidebar.text_input("Modelo", value=default_model)

    # Render do sidebar específico da personagem (se existir)
    if hasattr(service, "render_sidebar"):
        service.render_sidebar(st)  # cada serviço define seus campos/flags próprios

    # Prompt
    st.markdown(f"### {char}")
    prompt = st.text_area("Mensagem", height=160, placeholder="Escreva a cena / fala...")

    # Botão
    if st.button("Enviar", type="primary", use_container_width=True):
        if not prompt.strip():
            st.warning("Escreva algo antes de enviar.")
            return

        # Usuário lógico (pode vir da sessão/autenticação)
        usuario = st.session_state.get("usuario_key", "local")

        try:
            resposta = service.gerar_resposta(usuario=usuario, prompt_usuario=prompt, model=model)
            st.markdown(resposta)
        except Exception as e:
            st.exception(e)


if __name__ == "__main__":
    main()


