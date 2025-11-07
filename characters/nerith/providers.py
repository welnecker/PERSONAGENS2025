# characters/nerith/providers.py
from __future__ import annotations
import os
from typing import Dict, Tuple, Optional
import streamlit as st
from huggingface_hub import InferenceClient

# ==========================
# Catálogo de provedores/modelos
# ==========================
PROVIDERS: Dict[str, Dict[str, object]] = {
    # FLUX nativo HF
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "sdxl": False,
        "size": "1024x1024",
    },

    # SDXL base via Nscale
    "HF • SDXL (nscale)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sdxl": True,
        "size": "1152x896",
    },

    # SDXL Refiner (2 estágios simples)
    "HF • SDXL (nscale + Refiner)": {
        "provider": "huggingface-nscale",
        "model": "stabilityai/stable-diffusion-xl-refiner-1.0",
        "sdxl": True,
        "refiner": True,
        "size": "1152x896",
    },

    # FAL: Stable Image Ultra
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "sdxl": False,
        "size": "1024x1024",
    },

    # FAL: Dark Fantasy Flux
    "FAL • Dark Fantasy Flux": {
        "provider": "fal-ai",
        "model": "nerijs/dark-fantasy-illustration-flux",
        "sdxl": False,
        "size": "1024x1024",
    },

    # FAL: SDXL-Lightning (distilled) — guidance ≤ 2.0
    "FAL • SDXL Lightning": {
        "provider": "fal-ai",
        "model": "ByteDance/SDXL-Lightning",
        "sdxl": True,
        "lightning": True,
        "size": "1024x1024",
    },

    # FAL: Qwen Image Studio (Realism)
    "FAL • Qwen Image Studio (Realism)": {
        "provider": "fal-ai",
        "model": "prithivMLmods/Qwen-Image-Studio-Realism",
        "sdxl": False,
        "size": "1024x1024",
        "qwen": True,
    },

    # FAL: Qwen Image (oficial)
    "FAL • Qwen Image": {
        "provider": "fal-ai",
        "model": "Qwen/Qwen-Image",
        "sdxl": False,
        "size": "1024x1024",
        "qwen": True,
    },
}

# Resoluções “boas” para SDXL
SDXL_SIZES = {
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)": (896, 1152),
    "1216×832 (wide)": (1216, 832),
    "832×1216 (tall)": (832, 1216),
    "1024×1024": (1024, 1024),
}


def _get_hf_token() -> str:
    tok = (
        str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
        str(st.secrets.get("HF_TOKEN", "")) or
        os.environ.get("HUGGINGFACE_API_KEY", "") or
        os.environ.get("HF_TOKEN", "")
    )
    if not tok.strip():
        raise RuntimeError("⚠️ Faltando token: defina HUGGINGFACE_API_KEY ou HF_TOKEN")
    return tok.strip()


def get_client(provider: Optional[str]) -> InferenceClient:
    """
    Cria um InferenceClient adequado ao provider:
    - "huggingface-nscale"/"nscale": usa provider dedicado com api_key
    - "fal-ai": usa token HF normal (router faz o proxy)
    - default ("huggingface"): token normal
    """
    pv = (provider or "").lower().strip()
    token = _get_hf_token()
    if pv in ("huggingface-nscale", "nscale", "hf-nscale"):
        return InferenceClient(provider="nscale", api_key=token)
    return InferenceClient(token=token)


def parse_size(sz: str) -> Tuple[int, int]:
    w, h = map(int, sz.lower().split("x"))
    return w, h
