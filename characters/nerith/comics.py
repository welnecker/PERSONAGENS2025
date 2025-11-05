# characters/nerith/comics.py
from __future__ import annotations
import os, io, textwrap, random
from typing import Callable, List, Dict
from PIL import Image, ImageDraw, ImageFont
from huggingface_hub import InferenceClient
import streamlit as st

# ——— Client HF/FAL ———
def _hf_client() -> InferenceClient:
    token = st.secrets.get("HF_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Defina HF_TOKEN em st.secrets ou variável de ambiente.")
    return InferenceClient(provider="fal-ai", api_key=token)

# ——— Balão de fala ———
def _draw_speech_balloon(
    img: Image.Image,
    text: str,
    box: tuple[int,int,int,int],         # (x0, y0, x1, y1)
    tail_anchor: tuple[str,str] = ("left","bottom"),
    font_path: str | None = None,
    font_size: int = 28,
    padding: int = 14,
) -> None:
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    r = 22
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(255,255,255), outline=(0,0,0), width=4)

    try:
        font = ImageFont.truetype(font_path or "arial.ttf", font_size)
    except:
        font = ImageFont.load_default()

    width_text = x1 - x0 - 2*padding
    for ln in textwrap.wrap(text, width=32):
        draw.text((x0+padding, y0+padding), ln, font=font, fill=(0,0,0))
        y0 += font_size + 6

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

# ——— Prompt “estilo HQ” ———
def _build_comic_prompt(scene_desc: str, nsfw_on: bool) -> str:
    style = (
        "comic panel, dynamic composition, bold ink outlines, cel shading, halftone texture, "
        "high detail, dramatic lighting, 1x1 aspect, clean background separation"
    )
    guard = "" if nsfw_on else "sfw, tasteful, no nudity, implied romance only,"
    return f"{guard} {style}. Scene: {scene_desc}"

# ——— Escolhe falas recentes p/ balões ———
def _pick_dialog_balloons(hist_docs: List[Dict]) -> List[str]:
    balloons: List[str] = []
    for d in reversed(hist_docs):
        u = (d.get("mensagem_usuario") or "").strip()
        a = (d.get("resposta_nerith") or d.get("resposta_mary") or "").strip()
        if a and len(balloons) < 1:
            balloons.append(a.split("\n")[0][:140])
        if u and len(balloons) < 2:
            balloons.append(u.split("\n")[0][:140])
        if len(balloons) >= 2:
            break
    if not balloons:
        balloons = ["(Nerith aproxima e fala baixo…)", "Conduz para o canto, agora."]
    return balloons[:2]

# ——— Função ÚNICA chamada pela UI/Service ———
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    model_name: str = "briaai/FIBO",
    size: str = "1024x1024",
    title: str = "🎞️ Quadrinho (beta)"
) -> None:
    st.markdown(f"### {title}")
    colA, colB = st.columns([3,1])
    nsfw_on = colA.toggle("NSFW liberado", value=False)
    gen = colB.button("Gerar quadrinho", use_container_width=True)
    if not gen:
        return

    # 1) contexto
    try:
        docs = get_history_docs_fn() or []
    except:
        docs = []
    scene_desc = scene_text_provider() or "night alley, rain, two figures confronting each other."

    # 2) geração base
    client = _hf_client()
    prompt = _build_comic_prompt(scene_desc, nsfw_on)
    with st.spinner("Gerando painel…"):
        img = client.text_to_image(prompt, model=model_name, size=size)

    # 3) balões
    w, h = img.size
    pad = 24
    b1 = (pad, pad, int(w*0.55), int(h*0.33))
    b2 = (int(w*0.45), int(h*0.62), w-pad, h-pad)
    balloons = _pick_dialog_balloons(docs)
    _draw_speech_balloon(img, balloons[0], b1, tail_anchor=("left","bottom"))
    if len(balloons) > 1:
        _draw_speech_balloon(img, balloons[1], b2, tail_anchor=("right","top"))

    # 4) exibir & baixar
    st.image(img, caption="Painel em estilo HQ")
    buf = io.BytesIO(); img.save(buf, format="PNG")
    st.download_button("⬇️ Baixar PNG", data=buf.getvalue(), file_name="nerith_quadrinho.png", mime="image/png")
