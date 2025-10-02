# core/database.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Iterable, Tuple
from threading import RLock
import uuid

from .config import settings

# ---------- Fallback em memória (sempre disponível) ----------
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()

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

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if self._match(d, filt)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda x: x.get(key), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        return rows

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filt=filt, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if self._match(d, filt):
                    if "$set" in update:
                        d.update(update["$set"])
                    else:
                        d.update(update)
                    return
            if upsert:
                doc = dict(filt)
                if "$set" in update:
                    doc.update(update["$set"])
                self.insert_one(doc)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            before = len(rows)
            rows[:] = [d for d in rows if not self._match(d, filt)]
            return before - len(rows)


# ---------- Backend Mongo (opcional) ----------
_CLIENT = None
_DB = None
_USING_MONGO = False
_MONGO_ERR = ""

try:
    # Só tenta Mongo se as 3 variáveis estiverem presentes
    if settings.MONGO_USER and settings.MONGO_PASS and settings.MONGO_CLUSTER:
        from urllib.parse import quote_plus
        from pymongo import MongoClient
        from bson import ObjectId

        uri = (
            f"mongodb+srv://{quote_plus(settings.MONGO_USER)}:{quote_plus(settings.MONGO_PASS)}"
            f"@{settings.MONGO_CLUSTER}/?retryWrites=true&w=majority&appName={settings.APP_NAME}"
        )
        _CLIENT = MongoClient(uri, serverSelectionTimeoutMS=6000)
        # ping para validar
        _CLIENT.admin.command("ping")
        _DB = _CLIENT.get_database(settings.MONGO_DB or settings.APP_NAME)
        _USING_MONGO = True
    else:
        _USING_MONGO = False
except Exception as e:
    _USING_MONGO = False
    _MONGO_ERR = str(e)

class MongoCollection:
    def __init__(self, name: str):
        self.col = _DB.get_collection(name)

    @staticmethod
    def _normalize_id(doc: Dict[str, Any]) -> Dict[str, Any]:
        if doc and "_id" in doc:
            try:
                # ObjectId -> str
                from bson import ObjectId
                if isinstance(doc["_id"], ObjectId):
                    doc = dict(doc)
                    doc["_id"] = str(doc["_id"])
            except Exception:
                pass
        return doc

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        res = self.col.insert_one(doc)
        return {"inserted_id": str(res.inserted_id)}

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        cur = self.col.find(filt or {})
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(int(limit))
        for d in cur:
            yield self._normalize_id(d)

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if sort:
            doc = self.col.find_one(filt or {}, sort=sort)
        else:
            doc = self.col.find_one(filt or {})
        return self._normalize_id(doc) if doc else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        self.col.update_one(filt, update, upsert=upsert)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        res = self.col.delete_many(filt or {})
        return int(getattr(res, "deleted_count", 0))


def get_col(name: str):
    """Retorna uma coleção Mongo, se disponível; senão memória."""
    if _USING_MONGO and _DB is not None:
        return MongoCollection(name)
    return MemoryCollection(name)


def db_status() -> str:
    if _USING_MONGO:
        return f"MongoDB Atlas ✅ ({settings.MONGO_CLUSTER})"
    if _MONGO_ERR:
        return f"MongoDB ⚠️ fallback para memória — erro: {(_MONGO_ERR[:120] + '...') if len(_MONGO_ERR) > 120 else _MONGO_ERR}"
    return "MongoDB desativado → usando memória"
