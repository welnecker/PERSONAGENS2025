# characters/nerith/sdxl_nscale.py

from huggingface_hub import InferenceClient
import os
import time
import pathlib

def generate_sdxl_nscale(
    prompt: str,
    model: str = "stabilityai/stable-diffusion-xl-base-1.0",
    outdir: str = "outputs",
):
    """
    Gera imagem usando SDXL via HuggingFace (provider=nscale).
    Funciona 100% remoto, incluindo no Streamlit Cloud.
    """

    client = InferenceClient(
        provider="nscale",
        api_key=os.environ["HF_TOKEN"],
    )

    img = client.text_to_image(
        prompt,
        model=model,
    )

    pathlib.Path(outdir).mkdir(parents=True, exist_ok=True)
    filename = f"sdxl_{int(time.time())}.png"
    path = os.path.join(outdir, filename)
    img.save(path)

    return path
