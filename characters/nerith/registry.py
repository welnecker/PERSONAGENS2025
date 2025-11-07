# characters/nerith/registry.py
"""
Sub-registry da personagem Nerith.

Lido pelo registry global (characters/registry.py) para popular o seletor
de modelos da sidebar. Apenas `list_models()` é obrigatório.

Compatível com o comics.py modular final:
- FLUX.1-dev
- SDXL (nscale)
- SDXL (nscale + Refiner)
- FAL • Stable Image Ultra
- FAL • Dark Fantasy Flux
- FAL • SDXL Lightning
- FAL • Qwen Image Studio (Realism)
- FAL • Qwen Image
"""

# =========================
# Modelos exibidos na UI
# =========================
MODELS = [
    "HF • FLUX.1-dev",
    "HF • SDXL (nscale)",
    "HF • SDXL (nscale + Refiner)",
    "FAL • Stable Image Ultra",
    "FAL • Dark Fantasy Flux",
    "FAL • SDXL Lightning",
    "FAL • Qwen Image Studio (Realism)",
    "FAL • Qwen Image",
]

def list_models():
    """Retorna a lista de modelos para a Nerith."""
    return MODELS


# =========================
# Auxiliares opcionais
# =========================

# ⚠️ Os nomes aqui devem EXISTIR no PRESETS do arquivo presets.py
DEFAULT_PRESET_BY_MODEL = {
    "HF • FLUX.1-dev":                "FLUX • Nerith HQ",
    "HF • SDXL (nscale)":             "SDXL • Nerith Comic (Adulto)",
    "HF • SDXL (nscale + Refiner)":   "SDXL • Nerith Comic (Adulto)",
    "FAL • Stable Image Ultra":       "FLUX • Nerith HQ",             # fallback seguro
    "FAL • Dark Fantasy Flux":        "FLUX • Nerith Dark Fantasy",
    "FAL • SDXL Lightning":           "SDXL • Nerith Comic (Adulto)",
    "FAL • Qwen Image Studio (Realism)": "Qwen • Nerith Realism Comic",
    "FAL • Qwen Image":               "Qwen • Nerith Realism Comic",
}

def default_preset_for(model_name: str) -> str:
    """Sugere o preset padrão a partir do nome do modelo."""
    return DEFAULT_PRESET_BY_MODEL.get(model_name, "FLUX • Nerith HQ")


# Resoluções recomendadas para SDXL (coerentes com providers.py)
SDXL_SIZES = {
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)":   (896, 1152),
    "1216×832 (wide)":       (1216, 832),
    "832×1216 (tall)":       (832, 1216),
    "1024×1024":             (1024, 1024),
}

def list_sdxl_sizes():
    """Dict de tamanhos SDXL para uso opcional na UI."""
    return SDXL_SIZES


__all__ = [
    "MODELS",
    "list_models",
    "DEFAULT_PRESET_BY_MODEL",
    "default_preset_for",
    "SDXL_SIZES",
    "list_sdxl_sizes",
]
