# core/repositories.py
from typing import Any, Dict, List, Optional
from .database import get_col

# coleções
_state  = lambda: get_col("state_data")
_hist   = lambda: get_col("history")
_events = lambda: get_col("events")

# ---------- Fatos ----------
def get_facts(usuario: str) -> Dict[str, Any]:
    d = _state().find_one({"usuario": usuario})
    return d.get("fatos", {}) if d else {}

def get_fact(usuario: str, key: str, default: Any = None) -> Any:
    d = _state().find_one({"usuario": usuario})
    if not d:
        return default
    cur = d.get("fatos", {})
    # acesso raso ou pontilhado
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

def set_fact(usuario: str, key: str, value: Any, meta: Optional[Dict[str, Any]] = None) -> None:
    meta = meta or {}
    _state().update_one(
        {"usuario": usuario},
        {"$set": {f"usuario": usuario, f"fatos.{key}": value, "meta": meta}},
        upsert=True
    )

# ---------- Histórico ----------
def save_interaction(usuario: str, mensagem_usuario: str, resposta_mary: str, model_tag: str) -> None:
    _hist().insert_one({
        "usuario": usuario,
        "mensagem_usuario": mensagem_usuario,
        "resposta_mary": resposta_mary,
        "model": model_tag,
    })

def get_history_docs(usuario: str, limit: int = 400) -> List[Dict[str, Any]]:
    return list(_hist().find({"usuario": usuario}, sort=[("_id", 1)], limit=limit))

def delete_user_history(usuario: str) -> int:
    return _hist().delete_many({"usuario": usuario})

def delete_last_interaction(usuario: str) -> bool:
    docs = list(_hist().find({"usuario": usuario}))
    if not docs:
        return False
    last_id = docs[-1]["_id"]
    return _hist().delete_many({"_id": last_id}) > 0

# ---------- Eventos ----------
def register_event(usuario: str, tipo: str, descricao: str, local: Optional[str], extra: Optional[Dict[str, Any]] = None) -> None:
    _events().insert_one({
        "usuario": usuario,
        "tipo": tipo,
        "descricao": descricao,
        "local": local,
        "extra": extra or {},
    })

def list_events(usuario: str, limit: int = 5) -> List[Dict[str, Any]]:
    docs = list(_events().find({"usuario": usuario}, sort=[("_id", -1)], limit=limit))
    return docs

# ---------- Utilidades ----------
def last_event(usuario: str, tipo: str) -> Optional[Dict[str, Any]]:
    return _events().find_one({"usuario": usuario, "tipo": tipo}, sort=[("_id", -1)])

def delete_all_user_data(usuario: str) -> Dict[str, int]:
    return {
        "hist": _hist().delete_many({"usuario": usuario}),
        "state": _state().delete_many({"usuario": usuario}),
        "eventos": _events().delete_many({"usuario": usuario}),
        "perfil": 0,
    }
