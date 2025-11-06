# characters/nerith/comics.py
from __future__ import annotations
import os, io, textwrap, random
from typing import Callable, List, Dict, Tuple
from PIL import Image, ImageDraw, ImageFont
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

def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
           str(st.secrets.get("HF_TOKEN", "")) or
           os.environ.get("HUGGINGFACE_API_KEY", "") or
           os.environ.get("HF_TOKEN", ""))
    tok = (tok or "").strip()
    if not tok:
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok

def _hf_client(provider: str) -> InferenceClient:
    # OBS: sua base usa um cliente compatível com `provider` (wrapper interno).
    # Se estiver usando o InferenceClient padrão da HF, adapte para `InferenceClient(api_key=...)`.
    return InferenceClient(provider=provider, api_key=_get_hf_token())

# ======================
# Balão de fala
# ======================
def _wrap_text_to_box(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                      max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        wbox = draw.textbbox((0, 0), test, font=font)
        width = wbox[2] - wbox[0]
        if width <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # fallback para textos sem espaços
    final: List[str] = []
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            final.append(ln)
            continue
        approx = max(8, int(len(ln) * (max_width / max(1, bbox[2]))))
        final.extend(textwrap.wrap(ln, width=max(8, approx)))
    return final

def _draw_speech_balloon(
    img: Image.Image,
    text: str,
    box: Tuple[int, int, int, int],     # (x0, y0, x1, y1)
    tail_anchor: Tuple[str, str] = ("left", "bottom"),
    font_path: str | None = None,
    font_size: int = 28,
    padding: int = 14,
) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    r = 22
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(255, 255, 255), outline=(0, 0, 0), width=4)

    try:
        font = ImageFont.truetype(font_path or "arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    inner_w = (x1 - x0) - 2 * padding
    lines = _wrap_text_to_box(draw, text, font, inner_w)
    cy = y0 + padding
    for ln in lines:
        draw.text((x0 + padding, cy), ln, font=font, fill=(0, 0, 0))
        bbox = draw.textbbox((0, 0), ln, font=font)
        line_h = (bbox[3] - bbox[1]) if bbox else (font_size + 6)
        cy += line_h + 6

    # rabicho
    tail_w, tail_h = 36, 28
    if tail_anchor == ("left", "bottom"):
        pts = [(box[0] + 40, box[3]), (box[0] + 40 + tail_w, box[3]), (box[0] + 40, box[3] + tail_h)]
    elif tail_anchor == ("right", "bottom"):
        pts = [(box[2] - 40, box[3]), (box[2] - 40 - tail_w, box[3]), (box[2] - 40, box[3] + tail_h)]
    elif tail_anchor == ("left", "top"):
        pts = [(box[0] + 40, box[1]), (box[0] + 40 + tail_w, box[1]), (box[0] + 40, box[1] - tail_h)]
    else:  # ("right","top")
        pts = [(box[2] - 40, box[1]), (box[2] - 40 - tail_w, box[1]), (box[2] - 40, box[1] - tail_h)]
    draw.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))

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
# Balões (histórico)
# ======================
def _pick_dialog_balloons(hist_docs: List[Dict]) -> List[str]:
    balloons: List[str] = []
    for d in reversed(hist_docs):
        u = (d.get("mensagem_usuario") or "").strip()
        a = (d.get("resposta_nerith") or d.get("resposta_mary") or "").strip()
        if a and len(balloons) < 1:
            balloons.append(a.split("\n")[0][:180])
        if u and len(balloons) < 2:
            balloons.append(u.split("\n")[0][:180])
        if len(balloons) >= 2:
            break
    if not balloons:
        balloons = ["(Nerith aproxima e fala baixo…)", "Conduz para o canto, agora."]
    return balloons[:2]

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

        # Seleção de modelo/provedor
        prov_key = ui.selectbox(
            "Modelo para quadrinho",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        provider = cfg.get("provider", "fal-ai")
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
            docs = get_history_docs_fn() or []
        except Exception:
            docs = []
        scene_desc = scene_text_provider() or "night alley, rain, two figures confronting each other."

        # 2) geração base (usa provider + modelo selecionados)
        client = _hf_client(provider)
        prompt = _build_comic_prompt(scene_desc, nsfw_on)
        with ui.spinner("Gerando painel…"):
            # sua camada de cliente aceita (prompt, model=..., size=...)
            img = client.text_to_image(prompt, model=model_name, size=size)

        # 3) balões
        w, h = img.size
        pad = 24
        b1 = (pad, pad, int(w * 0.55), int(h * 0.33))
        b2 = (int(w * 0.45), int(h * 0.62), w - pad, h - pad)
        balloons = _pick_dialog_balloons(docs)
        _draw_speech_balloon(img, balloons[0], b1, tail_anchor=("left", "bottom"))
        if len(balloons) > 1:
            _draw_speech_balloon(img, balloons[1], b2, tail_anchor=("right", "top"))

        # 4) exibir & baixar
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
