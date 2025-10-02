from typing import Dict, Any, List
import streamlit as st
from core.repositories import get_fact, set_fact
from core.common.sidebar_types import FieldSpec
from core.common.base_service import BaseCharacter

def _visible(field: FieldSpec, values: Dict[str, Any]) -> bool:
    if not field.visible_if: return True
    return all(values.get(k) == v for k, v in field.visible_if.items())

def render_sidebar(svc: BaseCharacter, usuario_key: str) -> Dict[str, Any]:
    st.sidebar.header(svc.name)
    values: Dict[str, Any] = {}
    schema: List[FieldSpec] = svc.sidebar_schema()

    # load
    for f in schema:
        cur = get_fact(usuario_key, f.key, f.default)
        values[f.key] = cur if cur is not None else f.default

    # widgets
    for f in schema:
        if not _visible(f, values): continue

        if f.compute:
            try: values[f.key] = f.compute(values)
            except Exception: pass
            st.sidebar.caption(f"{f.label}: {values.get(f.key)}")
            continue
        if f.read_only:
            st.sidebar.caption(f"{f.label}: {values.get(f.key)}")
            continue

        if f.type == "bool":
            values[f.key] = st.sidebar.checkbox(f.label, bool(values.get(f.key, f.default)), help=f.help)
        elif f.type == "text":
            values[f.key] = st.sidebar.text_input(f.label, str(values.get(f.key, f.default) or ""), help=f.help)
        elif f.type == "int":
            values[f.key] = st.sidebar.number_input(f.label, value=int(values.get(f.key, f.default) or 0), step=1, help=f.help)
        elif f.type == "float":
            values[f.key] = st.sidebar.number_input(f.label, value=float(values.get(f.key, f.default) or 0.0), step=0.1, help=f.help)
        elif f.type == "select":
            ch = f.choices or []
            cur = values.get(f.key, f.default)
            if ch and cur not in ch: cur = ch[0]
            values[f.key] = st.sidebar.selectbox(f.label, ch, index=(ch.index(cur) if cur in ch else 0), help=f.help)
        elif f.type == "datetime":
            values[f.key] = st.sidebar.text_input(f.label, str(values.get(f.key, f.default) or ""), help=f.help, placeholder="YYYY-MM-DD HH:MM")
        elif f.type == "note":
            st.sidebar.info(values.get(f.key, f.default) or "")
        else:
            values[f.key] = st.sidebar.text_input(f.label, str(values.get(f.key, f.default) or ""), help=f.help)

    if st.sidebar.button("Salvar preferências"):
        for f in schema:
            if not f.read_only and f.key in values:
                set_fact(usuario_key, f.key, values[f.key], {"fonte":"sidebar"})
        try:
            svc.on_sidebar_change(usuario_key, values)
            st.sidebar.success("Preferências salvas.")
        except Exception as e:
            st.sidebar.warning(f"Salvo com avisos: {e}")

    return values

