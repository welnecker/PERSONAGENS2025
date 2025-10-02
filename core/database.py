# core/database.py
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from threading import RLock
import uuid
import os

from .config import settings

# ------------------------------------------------------------
# Backend detection
# ------------------------------------------------------------
_BACKEND = (os.getenv("DB_BACKEND") or settings.DB_BACKEND or "memory").lower()
_HAVE_PYMONGO = False
try:
    if _BACKEND == "mongo":
        import pymongo  # type: ignore
        from pymongo.collection import Collection  # type: ignore
        _HAVE_PYMONGO = True
except Exception:
    _HAVE_PYMONGO = False

# ------------------------------------------------------------
# Memory backend (fallback)
# ------------------------------------------------------------
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()


def _dict_get_dotted(d: Dict[str, Any], dotted: str) -> Any:
    """Lê chave com notação 'a.b.c' em dicts aninhados (para projeção/compat)."""
    cur: Any = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


class MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        _STORE.setdefault(name, [])

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            d = dict(doc)
            d.setdefault("_id", str(uuid.uuid4()))
            _STORE[self.name].append(d)
            return {"inserted_id": d["_id"]}

    def _match(self, doc: Dict[str, Any], filt: Optional[Dict[str, Any]]) -> bool:
        if not filt:
            return True
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def _apply_projection(self, row: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
        if not projection:
            return row
        # comportamento simples: se proj tem chaves positivas (1), retorna só essas
        include_keys = [k for k, v in projection.items() if v]
        if include_keys:
            out = {"_id": row.get("_id")}
            for k in include_keys:
                val = _dict_get_dotted(row, k)
                if val is not None:
                    # suporte simples apenas para níveis superiores
                    if "." in k:
                        # insere achatado
                        out[k] = val
                    else:
                        out[k] = val
            return out
        return row

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if self._match(d, filt)]
        if sort and isinstance(sort, list):
            for key, direction in reversed(sort):
                rows.sort(key=lambda x: x.get(key), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        if projection:
            rows = [self._apply_projection(r, projection) for r in rows]
        return rows

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filt=filt, projection=projection, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if self._match(d, filt):
                    if "$set" in update and isinstance(update["$set"], dict):
                        d.update(update["$set"])
                    else:
                        d.update(update)
                    return
            if upsert:
                doc = dict(filt)
                if "$set" in update and isinstance(update["$set"], dict):
                    doc.update(update["$set"])
                self.insert_one(doc)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            before = len(rows)
            rows[:] = [d for d in rows if not self._match(d, filt)]
            return before - len(rows)


# ------------------------------------------------------------
# Mongo backend (preferred if configured)
# ------------------------------------------------------------
_mongo_client = None
_mongo_db = None


def _mongo_connect():
    global _mongo_client, _mongo_db
    if _mongo_client is not None:
        return
    uri = settings.mongo_uri()
    if not uri:
        raise RuntimeError("Mongo configurado sem credenciais válidas.")
    _mongo_client = pymongo.MongoClient(uri)  # type: ignore[name-defined]
    dbname = os.getenv("MONGO_DB") or settings.APP_NAME
    _mongo_db = _mongo_client.get_database(dbname)


def _to_py(d: Dict[str, Any]) -> Dict[str, Any]:
    """Converte ObjectId para str; retorna dict copiado."""
    if not d:
        return d
    out = dict(d)
    _id = out.get("_id")
    try:
        from bson import ObjectId  # type: ignore
        if isinstance(_id, ObjectId):
            out["_id"] = str(_id)
    except Exception:
        pass
    return out


class MongoCollection:
    def __init__(self, coll: "Collection"):
        self._c = coll

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        r = self._c.insert_one(doc)
        return {"inserted_id": str(getattr(r, "inserted_id", ""))}

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        cur = self._c.find(filter=filt or {}, projection=projection)
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(int(limit))
        for d in cur:
            yield _to_py(d)

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if sort:
            # PyMongo não aceita sort direto em find_one; usar find().sort().limit(1)
            cur = self._c.find(filter=filt or {}, projection=projection).sort(sort).limit(1)
            doc = next(iter(cur), None)
        else:
            doc = self._c.find_one(filter=filt or {}, projection=projection)
        return _to_py(doc) if doc else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        self._c.update_one(filter=filt, update=update, upsert=upsert)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        r = self._c.delete_many(filter=filt)
        return int(getattr(r, "deleted_count", 0))


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def get_col(name: str):
    """
    Retorna uma 'coleção' com interface básica (insert/find/find_one/update/delete).
    Usa Mongo se DB_BACKEND=mongo e pymongo estiver disponível; caso contrário, memória.
    """
    if _BACKEND == "mongo" and _HAVE_PYMONGO:
        try:
            _mongo_connect()
            return MongoCollection(_mongo_db.get_collection(name))  # type: ignore[arg-type]
        except Exception:
            # fallback silencioso para memória se conexão falhar
            return MemoryCollection(name)
    # memory
    return MemoryCollection(name)


def db_status() -> Tuple[str, str]:
    """
    Retorna (backend, detalhe) para debug na UI.
    """
    if _BACKEND == "mongo":
        if not _HAVE_PYMONGO:
            return ("memory", "Mongo solicitado, mas pacote 'pymongo' não está instalado. Usando memória.")
        try:
            _mongo_connect()
            host = settings.MONGO_CLUSTER or "?"
            user = settings.MONGO_USER or "?"
            dbn = os.getenv("MONGO_DB") or settings.APP_NAME
            return ("mongo", f"user={user} host={host} db={dbn}")
        except Exception as e:
            return ("memory", f"Mongo indisponível ({e}). Usando memória.")
    return ("memory", "Backend padrão em memória.")
