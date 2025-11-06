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
# PRESETS de prompt (padrões + do usuário)
# ======================

# Presets padrão (imutáveis)
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full-body shot, bold ink outlines, cel shading, "
            "dramatic rimlight, wet reflections, gritty detail, dynamic angle, halftone texture, 1x1 aspect; "
            "female dark-elf warrior from Elysarix; blue-slate luminous skin with faint inner glow; "
            "metallic silver long hair; predatory green eyes; athletic hourglass body; "
            "firm medium breasts; wide hips; large round muscular butt; strong thighs; feline posture; "
            "silver sensory tendrils on arms and neck; retractable curved blade-tail; "
            "predatory stalking, low lighting, wet ground, neon reflections; tendrils scanning the air"
        ),
        "negative": (
            "no kissing, no near-kiss, no couple pose, no romantic framing, "
            "no gentle intimacy, avoid romance cinematography"
        ),
        "style": "gritty sci-fi noir, rain, reflections, power composition",
    },
    "Nerith • Dominante": {
        "positive": (
            "high-end comic panel, three-quarter full body, bold ink, cel shading, "
            "dramatic rimlight; Elysarix dark-elf; blue-slate glowing skin; metallic silver hair; "
            "green neon eyes; hourglass, hips emphasized; tail-blade half raised; tendrils active; "
            "dominant sensual posture; intense teasing gaze without romance"
        ),
        "negative": (
            "no kissing, no couple, no soft romance, no coy cheek touching, "
            "no romantic cinematography"
        ),
        "style": "cinematic backlight, smoky atmosphere, halftone accents",
    },
    "Nerith • Batalha": {
        "positive": (
            "full-body combat stance, explosive motion lines, debris, sparks; "
            "tail-blade extended; tendrils reacting; muscles flexing; high detail; "
            "bold inks, cel shading, halftone texture"
        ),
        "negative": "no couple, no kiss, no romance",
        "style": "dynamic action composition, low-angle shot",
    },
}

def _preset_store() -> Dict[str, Dict[str, str]]:
    """Armazena os presets do usuário na sessão."""
    return st.session_state.setdefault("nerith_comic_user_presets", {})

def get_all_presets() -> Dict[str, Dict[str, str]]:
    """Mescla padrão + do usuário (preferência para os do usuário se houver nomes iguais)."""
    merged = dict(_DEFAULT_PRESETS)
    merged.update(_preset_store())
    return merged

def save_user_preset(name: str, data: Dict[str, str]) -> None:
    name = (name or "").strip()
    if not name:
        return
    store = _preset_store()
    # Mantém apenas campos usados
    store[name] = {
        "positive": data.get("positive", ""),
        "negative": data.get("negative", ""),
        "style": data.get("style", ""),
    }

def build_prompt_from_preset(preset: Dict[str, str], scene_desc: str, nsfw_on: bool) -> str:
    """Constrói o prompt final a partir do preset + cena + guarda NSFW."""
    guard = "" if nsfw_on else "sfw, no explicit nudity, no genitals visible, implied tension only,"
    pos = preset.get("positive", "").strip()
    neg = preset.get("negative", "").strip()
    sty = preset.get("style", "").strip()
    parts = [
        guard,
        pos,
        f"style: {sty}" if sty else "",
        f"Scene: {scene_desc}".strip(),
        f"NEGATIVE: {neg}" if neg else "",
    ]
    return " ".join(p for p in parts if p)


# ======================
# UI principal / chamada (com presets + edição + NSFW + sem balões)
# ======================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    # defaults serão substituídos pela seleção do usuário
    model_name: str = "briaai/FIBO",
    size: str = "1024x1024",
    title: str = "🎞️ Quadrinho (beta)",
    ui=None,
    key_prefix: str = "",
) -> None:
    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")
        ui.caption("• bloco de quadrinhos carregado")

        # ------------------------------
        # ✅ Seleção de MODELO
        # ------------------------------
        prov_key = ui.selectbox(
            "Modelo",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        model_name = cfg.get("model", model_name)
        size = cfg.get("size", size)

        # ------------------------------
        # ✅ Seleção de PRESET + Editor
        # ------------------------------
        all_presets = get_all_presets()
        preset_names = list(all_presets.keys())

        sel_preset = ui.selectbox(
            "Preset de Cena",
            options=preset_names,
            index=0,
            key=f"{key_prefix}_preset_sel",
        )
        cur = dict(all_presets.get(sel_preset, {}))  # cópia editável

        with ui.expander("✏️ Ajustar preset (opcional)", expanded=False):
            cur["positive"] = ui.text_area(
                "Positive Prompt",
                value=cur.get("positive", ""),
                height=140,
                key=f"{key_prefix}_preset_pos",
            )
            cur["negative"] = ui.text_area(
                "Negative Prompt",
                value=cur.get("negative", ""),
                height=90,
                key=f"{key_prefix}_preset_neg",
            )
            cur["style"] = ui.text_input(
                "Estilo Extra",
                value=cur.get("style", ""),
                key=f"{key_prefix}_preset_style",
            )

            cols_ps = ui.columns([3, 1])
            new_name = cols_ps[0].text_input(
                "Salvar como",
                value=f"{sel_preset} (meu)",
                key=f"{key_prefix}_preset_newname",
            )
            if cols_ps[1].button("💾 Salvar preset", key=f"{key_prefix}_savepreset"):
                save_user_preset(new_name, cur)
                ui.success(f"Preset salvo: {new_name}")

        # ------------------------------
        # ✅ NSFW + Botão Gerar
        # ------------------------------
        cA, cB = ui.columns([3, 1])
        nsfw_on = cA.toggle(
            "NSFW liberado",
            value=False,
            key=f"{key_prefix}_nsfw_toggle"
        )
        gen = cB.button(
            "Gerar quadrinho",
            key=f"{key_prefix}_gen_btn",
            use_container_width=True
        )
        if not gen:
            return

        # ------------------------------
        # ✅ Cena textual
        # ------------------------------
        try:
            _ = get_history_docs_fn()
        except Exception:
            pass
        scene_desc = scene_text_provider() or "Nerith em posição de combate, noite, chuva, neon."

        # ------------------------------
        # ✅ Construção de prompt via PRESET
        # ------------------------------
        prompt = build_prompt_from_preset(cur, scene_desc, nsfw_on)

        # ------------------------------
        # ✅ Conversão de 'size'
        # ------------------------------
        if isinstance(size, str) and "x" in size.lower():
            parts = size.lower().split("x")
            width, height = int(parts[0]), int(parts[1])
        else:
            width = height = 1024

        # ------------------------------
        # ✅ Geração (sem balões)
        # ------------------------------
        client = _hf_client()

        with st.spinner("Gerando painel…"):
            img = client.text_to_image(
                model=model_name,
                prompt=prompt,
                width=width,
                height=height,
            )

        # compat: se vier bytes
        if not isinstance(img, Image.Image):
            try:
                img = Image.open(io.BytesIO(img))
            except Exception:
                raise RuntimeError("Falha ao decodificar imagem retornada pela API.")

        # ------------------------------
        # ✅ Exibir e Download
        # ------------------------------
        ui.image(img, caption="Painel em estilo HQ", use_column_width=True)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        ui.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_quadrinho.png",
            mime="image/png",
            key=f"{key_prefix}_dl_btn",
        )

    except Exception as e:
        ui.error(f"Quadrinhos: {e}")
