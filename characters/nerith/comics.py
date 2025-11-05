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
    # chave -> {provider, model, size_default}
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
    return InferenceClient(provider=provider, api_key=_get_hf_token())

# ======================
# Balão de fala
# ======================
def _wrap_text_to_box(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                      max_width: int) -> List[str]:
    # quebra por palavras medindo com textbbox
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        wbox = draw.textbbox((0,0), test, font=font)
        width = wbox[2] - wbox[0]
        if width <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # fallback para textos muito longos sem espaços
    final: List[str] = []
    for ln in lines:
        if draw.textbbox((0,0), ln, font=font)[2] - draw.textbbox((0,0), ln, font=font)[0] <= max_width:
            final.append(ln); continue
        # força quebra aproximando largura por caracteres
        approx = max(8, int(len(ln) * (max_width / max(1, draw.textbbox((0,0), ln, font=font)[2]))))
        final.extend(textwrap.wrap(ln, width=max(8, approx)))
    return final

def _draw_speech_balloon(
    img: Image.Image,
    text: str,
    box: Tuple[int,int,int,int],         # (x0, y0, x1, y1)
    tail_anchor: Tuple[str,str] = ("left","bottom"),
    font_path: str | None = None,
    font_size: int = 28,
    padding: int = 14,
) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    r = 22
    # balão
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(255,255,255), outline=(0,0,0), width=4)

    try:
        font = ImageFont.truetype(font_path or "arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    inner_w = (x1 - x0) - 2*padding
    lines = _wrap_text_to_box(draw, text, font, inner_w)
    cy = y0 + padding
    for ln in lines:
        draw.text((x0 + padding, cy), ln, font=font, fill=(0,0,0))
        # altura baseada na métrica real
        bbox = draw.textbbox((0,0), ln, font=font)
        line_h = (bbox[3] - bbox[1]) if bbox else (font_size + 6)
        cy += line_h + 6

    # rabicho
    tail_w, tail_h = 36, 28
    if tail_anchor == ("left","bottom"):
        pts = [(box[0]+40, box[3]), (box[0]+40+tail_w, box[3]), (box[0]+40, box[3]+tail_h)]
    elif tail_anchor == ("right","bottom"):
        pts = [(box[2]-40, box[3]), (box[2]-40-tail_w, box[3]), (box[2]-40, box[3]+tail_h)]
    elif tail_anchor == ("left","top"):
        pts = [(box[0]+40, box[1]), (box[0]+40+tail_w, box[1]), (box[0]+40, box[1]-tail_h)]
    else:  # ("right","top")
        pts = [(box[2]-40, box[1]), (box[2]-40-tail_w, box[1]), (box[2]-40, box[1]-tail_h)]
    draw.polygon(pts, fill=(255,255,255), outline=(0,0,0))

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
    title: str = "🎞️ Quadrinho (beta)"
) -> None:
    st.markdown(f"### {title}")

    # Provider/model UI
    colA, colB = st.columns([3,2])
    prov_key = colA.selectbox("Provider/Modelo", options=list(PROVIDERS.keys()), index=0)
    nsfw_on  = colB.toggle("NSFW liberado", value=False)

    cfg = PROVIDERS.get(prov_key, next(iter(PROVIDERS.values())))
    provider = cfg["provider"]
    model_default = cfg["model"]
    size_default  = cfg["size"]

    col1, col2 = st.columns(2)
    model_name = col1.text_input("Model ID", value=model_default)
    size = col2.text_input("Tamanho", value=size_default)

    exp = st.expander("⚙️ Prompt (visualizar/editar)", expanded=False)
    scene_desc = scene_text_provider() or "night alley, rain, two figures confronting each other."
    prompt_preview = _build_comic_prompt(scene_desc, nsfw_on)
    user_prompt = exp.text_area("Prompt final", value=prompt_preview, height=140)

    gen = st.button("🎨 Gerar quadrinho", use_container_width=True)
    if not gen:
        return

    # 1) contexto
    try:
        docs = get_history_docs_fn() or []
    except Exception:
        docs = []

    # 2) geração base
    try:
        client = _hf_client(provider)
        with st.spinner("Gerando painel…"):
            img = client.text_to_image(user_prompt, model=model_name, size=size)
        if not isinstance(img, Image.Image):
            # huggingface client pode retornar bytes
            img = Image.open(io.BytesIO(img))
    except Exception as e:
        st.error(f"Falha ao gerar imagem: {str(e)[:300]}")
        return

    # 3) balões (layout simples 2 balões)
    w, h = img.size
    pad = max(20, int(min(w, h) * 0.02))
    b1 = (pad, pad, int(w*0.56), int(h*0.35))
    b2 = (int(w*0.44), int(h*0.60), w-pad, h-pad)
    balloons = _pick_dialog_balloons(docs)
    try:
        _draw_speech_balloon(img, balloons[0], b1, tail_anchor=("left","bottom"))
        if len(balloons) > 1:
            _draw_speech_balloon(img, balloons[1], b2, tail_anchor=("right","top"))
    except Exception as e:
        st.warning(f"Imagem gerada, mas houve erro ao desenhar balões: {str(e)[:200]}")

    # 4) exibir & baixar
    st.image(img, caption="Painel em estilo HQ", use_container_width=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    st.download_button("⬇️ Baixar PNG", data=buf.getvalue(),
                       file_name="nerith_quadrinho.png", mime="image/png")
