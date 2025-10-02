# core/repositories.py
from datetime import datetime
from typing import Any, Dict, List, Optional
import re

from .database import get_col

# --- Coleções (helpers) ---
def _hist():
    return get_col("mary_historia")

def _state():
    return get_col("mary_state")

def _events():
    return get_col("mary_eventos")

def _profile():
    return get_col("mary_perfil")

def _uq(usuario: str) -> Dict[str, Any]:
    """Filtro ancorado e case-insensitive por usuário."""
    return {"usuario": {"$regex": f"^{re.escape(usuario)}$", "$options": "i"}}

# -------- CRUD básico --------
def save_interaction(usuario: str, user_msg: str, mary_msg: str, modelo: str = "") -> None:
    _hist().insert_one({
        "usuario": usuario,
        "mensagem_usuario": user_msg,
        "resposta_mary": mary_msg,
        "modelo": modelo,
        "timestamp": datetime.utcnow().isoformat(),
    })

def get_history_docs(usuario: str, limit: int = 400) -> List[Dict[str, Any]]:
    cur = _hist().find(_uq(usuario)).sort([("_id", 1)]).limit(limit)
    return list(cur)

def set_fact(usuario: str, key: str, value: Any, meta: Optional[Dict[str, Any]] = None) -> None:
    _state().update_one(
        {"usuario": usuario},
        {"$set": {
            f"fatos.{key}": value,
            f"meta.{key}": (meta or {}),
            "atualizado_em": datetime.utcnow(),
        }},
        upsert=True,
    )

def get_fact(usuario: str, key: str, default=None):
    try:
        # NÃO passe projeção para database.find_one (API local trata 2º arg como "sort")
        d = _state().find_one({"usuario": usuario})
        if not d:
            return default
        fatos = d.get("fatos", {})
        # suporta caminho com pontos, ex.: "perfil.idade"
        cur = fatos
        for part in (key or "").split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return default if cur is None else cur
    except Exception:
        return default


def get_facts(usuario: str) -> dict:
    try:
        d = _state().find_one({"usuario": usuario})
        return d.get("fatos", {}) if d else {}
    except Exception:
        return {}


def register_event(
    usuario: str,
    tipo: str,
    descricao: str,
    local: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    _events().insert_one({
        "usuario": usuario,
        "tipo": tipo,
        "descricao": descricao,
        "local": local,
        "ts": datetime.utcnow(),
        "tags": [],
        "meta": meta or {},
    })

def last_event(usuario: str, tipo: str):
    return _events().find_one({**_uq(usuario), "tipo": tipo}, sort=[("ts", -1)])

def list_events(usuario: str, limit: int = 5) -> List[Dict[str, Any]]:
    cur = _events().find(_uq(usuario)).sort([("ts", -1)]).limit(limit)
    return list(cur)

# -------- Listagem utilitária --------
def list_interactions(usuario: str, limit: int = 400) -> List[Dict[str, Any]]:
    cur = _hist().find(_uq(usuario)).sort([("_id", 1)]).limit(limit)
    return list(cur)

# -------- Deleters --------
def delete_user_history(usuario: str) -> int:
    res = _hist().delete_many(_uq(usuario))
    return res.deleted_count

def delete_last_interaction(usuario: str) -> bool:
    doc = _hist().find_one(_uq(usuario), sort=[("_id", -1)])
    if not doc:
        return False
    _hist().delete_one({"_id": doc["_id"]})
    return True

def delete_all_user_data(usuario: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    out["hist"]    = _hist().delete_many(_uq(usuario)).deleted_count
    out["state"]   = _state().delete_many(_uq(usuario)).deleted_count
    out["eventos"] = _events().delete_many(_uq(usuario)).deleted_count
    out["perfil"]  = _profile().delete_many(_uq(usuario)).deleted_count
    return out

def reset_nsfw(usuario: str) -> None:
    """Força NSFW OFF e limpa locks de cena."""
    _state().update_one(
        {"usuario": usuario},
        {
            "$set": {"fatos.virgem": True},
            "$unset": {
                "fatos.cena_parceiro_ativo": "",
                "fatos.cena_parceiro_ativo_ts": "",
                "fatos.cena_parceiro_ttl_min": "",
            },
        },
        upsert=True,
    )
    _events().delete_many({**_uq(usuario), "tipo": "primeira_vez"})
