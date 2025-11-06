# characters/nerith/comics.py
from __future__ import annotations
import os, io
from typing import Callable, List, Dict
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st

# ======================
# Config / Providers
# ======================
PROVIDERS: Dict[str, Dict[str, str]] = {
    # chave -> {provider, model, size}
    "FAL • BRIA FIBO": {
        "provider": "fal-ai",
        "model": "briaai/FIBO",
        "size": "1024x1024",
    },
    "FAL • FLUX Schnell": {
        "provider": "fal-ai",
        "model": "black-forest-labs/FLUX.1-schnell",
        "size": "1024x1024",
    },
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "size": "1024x1024",
    },
}

# ======================
# Tokens / Client
# ======================
def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
           str(st.secrets.get("HF_TOKEN", "")) or
           os.environ.get("HUGGINGFACE_API_KEY", "") or
           os.environ.get("HF_TOKEN", ""))
    tok = (tok or "").strip()
    if not tok:
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok

def _hf_client() -> InferenceClient:
    # huggingface_hub.InferenceClient usa 'token'
    return InferenceClient(token=_get_hf_token())

# ======================
# Prompt HQ
# ======================
def _build_comic_prompt(scene_desc: str, nsfw_on: bool) -> str:
    style = (
        "comic panel, dynamic composition, bold ink outlines, cel shading, halftone texture, "
        "dramatic lighting, high detail, 1x1 aspect, clean background separation"
    )
    guard = "" if nsfw_on else "sfw, tasteful, no explicit nudity, implied intimacy only,"
    return f"{guard} {style}. Scene: {scene_desc}"

# ======================
# UI principal / chamada
# ======================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    # defaults serão substituídos pela seleção do usuário
    model_name: str = "briaai/FIBO",
    size: str = "1024x1024",
    title: str = "🎞️ Quadrinho (beta)",
    ui=None,                  # container (ex.: a sidebar)
    key_prefix: str = "",     # prefixo para keys únicas
) -> None:
    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")
        ui.caption("• bloco de quadrinhos carregado")

        # Seleção de modelo/provedor (mantido para extensibilidade futura)
        prov_key = ui.selectbox(
            "Modelo para quadrinho",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        # 'provider' fica reservado; InferenceClient padrão usa apenas token
        # Mantemos 'model_name' e 'size' conforme preset escolhido
        model_name = cfg.get("model", model_name)
        size = cfg.get("size", size)

        colA, colB = ui.columns([3, 1])
        nsfw_on = colA.toggle(
            "NSFW liberado",
            value=False,
            key=f"{key_prefix}_nsfw_toggle"
        )
        gen = colB.button(
            "Gerar quadrinho",
            key=f"{key_prefix}_gen_btn",
            use_container_width=True
        )

        if not gen:
            return

        # 1) contexto
        try:
            _ = get_history_docs_fn() or []  # mantido caso queira usar no futuro
        except Exception:
            pass
        scene_desc = scene_text_provider() or "night alley, rain, two figures confronting each other."

        # 2) geração base (Hugging Face Inference) — sem balões
        client = _hf_client()
        prompt = _build_comic_prompt(scene_desc, nsfw_on)

        # parse de size -> width/height
        if isinstance(size, str) and "x" in size.lower():
            w_str, h_str = size.lower().split("x", 1)
            width, height = int(w_str), int(h_str)
        elif isinstance(size, (tuple, list)) and len(size) == 2:
            width, height = int(size[0]), int(size[1])
        else:
            width = height = 1024

        # spinner deve ser sempre st.spinner (sidebar não tem .spinner)
        with st.spinner("Gerando painel…"):
            img = client.text_to_image(
                model=model_name,
                prompt=prompt,
                width=width,
                height=height,
            )

        # compat: se a lib retornar bytes em versões antigas
        if not isinstance(img, Image.Image):
            try:
                img = Image.open(io.BytesIO(img))
            except Exception:
                raise RuntimeError("Falha ao decodificar imagem retornada pela API.")

        # 3) exibir & baixar (imagem limpa, sem balões)
        ui.image(img, caption="Painel em estilo HQ", use_column_width=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        ui.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_quadrinho.png",
            mime="image/png",
            key=f"{key_prefix}_dl_btn"
        )

    except Exception as e:
        ui.error(f"Quadrinhos: {e}")
