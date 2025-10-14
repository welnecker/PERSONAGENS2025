# core/memoria_longa.py
from __future__ import annotations
import os, time, math, hashlib, json
from typing import List, Dict, Any, Optional, Tuple

# DB
try:
    from core.database import get_col
except Exception:
    get_col = None

# Embeddings (OpenAI >=1.0 recomendado; fallback determinístico)
def _embed_openai(text: str) -> Optional[List[float]]:
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
        if not api_key:
            return None
        client = OpenAI(api_key=api_key)
        model = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
        out = client.embeddings.create(model=model, input=text)
        return out.data[0].embedding
    except Exception:
        return None

def _embed_fallback(text: str, dim: int = 256) -> List[float]:
    # hashing simples e estável (não semântico, mas funcional como fallback)
    h = hashlib.sha256(text.encode("utf-8")).digest()
    base = list(h) * (math.ceil(dim / len(h)))
    base = base[:dim]
    # normaliza 0..1
    return [x / 255.0 for x in base]

def embed(text: str) -> List[float]:
    text = (text or "").strip()
    if not text:
        return _embed_fallback("")
    vec = _embed_openai(text)
    return vec if vec else _embed_fallback(text)

def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    return (dot / (na * nb + 1e-9))

def _col():
    if not callable(get_col):
        raise RuntimeError("core.database.get_col indisponível")
    return get_col("memoria_longa")

def ensure_indexes() -> None:
    try:
        col = _col()
        col.create_index([("usuario_key", 1), ("tags", 1), ("ts", -1)])
        col.create_index([("usuario_key", 1), ("hash", 1)], unique=True)
    except Exception:
        pass

def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

def save_fragment(usuario_key: str, texto: str, tags: List[str] | None = None) -> Optional[str]:
    """
    Salva um fragmento canônico (curto e informativo).
    Evita duplicatas por hash.
    """
    if not texto or not usuario_key:
        return None
    try:
        col = _col()
        vec = embed(texto)
        doc = {
            "usuario_key": usuario_key,
            "texto": texto,
            "tags": list(tags or []),
            "ts": time.time(),
            "hash": _hash(usuario_key + "||" + texto),
            "vec": vec,
        }
        col.update_one({"usuario_key": usuario_key, "hash": doc["hash"]},
                       {"$setOnInsert": doc}, upsert=True)
        return doc["hash"]
    except Exception:
        return None

def topk(usuario_key: str, query: str, k: int = 5, allow_tags: List[str] | None = None) -> List[Dict[str, Any]]:
    """
    Retorna top-K fragmentos por similaridade. Se allow_tags for dada, filtra.
    """
    if not query or not usuario_key:
        return []
    try:
        col = _col()
        q = {"usuario_key": usuario_key}
        if allow_tags:
            q["tags"] = {"$in": list(allow_tags)}
        cur = col.find(q).limit(400)  # traz um lote e ranqueia em memória (simples)
        vec_q = embed(query)
        scored = []
        for d in cur:
            v = d.get("vec") or []
            score = _cosine(vec_q, v) if v else 0.0
            scored.append((score, d))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [d for _, d in scored[:k]]
    except Exception:
        return []
