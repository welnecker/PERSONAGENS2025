# characters/nerith/registry.py
"""
Sub-registry da personagem Nerith.

Este módulo é lido pelo registry global (characters/registry.py) para
popular o seletor de modelos da sidebar. Apenas `list_models()` é
obrigatório. As demais funções/constantes são auxiliares e não
quebram nada se não forem usadas.

Compatível com o comics.py final:
- FLUX.1-dev
- SDXL (nscale)
- SDXL (nscale + Refiner)
- FAL • Stable Image Ultra
"""

# Modelos exibidos na UI (sidebar) da Nerith
MODELS = [
    "HF • FLUX.1-dev",
    "HF • SDXL (nscale)",
    "HF • SDXL (nscale + Refiner)",
    "FAL • Stable Image Ultra",
]

def list_models():
    """Retorna a lista de modelos para a Nerith."""
    return MODELS


# ======= Abaixo: auxiliares opcionais (usados se quiser integrar na UI) =======

# Preset padrão sugerido por modelo (chaves devem existir no comics.py/PRESETS)
DEFAULT_PRESET_BY_MODEL = {
    "HF • FLUX.1-dev": "FLUX • Nerith HQ",
    "HF • SDXL (nscale)": "SDXL • Nerith Cinematic",
    "HF • SDXL (nscale + Refiner)": "SDXL • Nerith Cinematic",
    "FAL • Stable Image Ultra": "FLUX • Nerith HQ",  # fallback aceitável
}

def default_preset_for(model_name: str) -> str:
    """Sugere o preset padrão a partir do nome do modelo."""
    return DEFAULT_PRESET_BY_MODEL.get(model_name, "FLUX • Nerith HQ")


# Resoluções recomendadas para SDXL (coerentes com comics.py)
SDXL_SIZES = {
    "1024×1024": (1024, 1024),
    "1152×896 (horizontal)": (1152, 896),
    "896×1152 (vertical)": (896, 1152),
    "1216×832 (wide)": (1216, 832),
    "832×1216 (tall)": (832, 1216),
}

def list_sdxl_sizes():
    """Devolve o dict de tamanhos SDXL para uso opcional na UI."""
    return SDXL_SIZES


__all__ = [
    "MODELS",
    "list_models",
    "DEFAULT_PRESET_BY_MODEL",
    "default_preset_for",
    "SDXL_SIZES",
    "list_sdxl_sizes",
]
