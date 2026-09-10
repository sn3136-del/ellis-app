"""Optimistic concurrency for an operator's exact reviewed issue revision."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json

from sqlalchemy import JSON, String, cast, inspect, select, update
from fastapi import HTTPException


def snapshot(row):
    return {column.key: deepcopy(getattr(row, column.key))
            for column in inspect(type(row)).columns}


def revision_sha256(values):
    body = json.dumps(values, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'),
                      default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))
    return hashlib.sha256(body.encode()).hexdigest()


def check_revision(row, expected):
    original = snapshot(row)
    if expected is not None and expected != revision_sha256(original):
        raise HTTPException(409, 'This issue changed after review; reload and review its current proposal.')
    return original


def save_revision(db, row, original):
    """Apply only the endpoint's status edits if every original column matches.

    The conditional UPDATE, rather than an earlier GET comparison, closes the
    race with a concurrent source proposal or operator action. Expiring the ORM
    instance discards pending attribute writes so commit cannot bypass the CAS.
    """
    cls = type(row)
    changes = {column.key: getattr(row, column.key)
               for column in inspect(cls).columns
               if inspect(row).attrs[column.key].history.has_changes()}
    changes.setdefault('status', row.status)
    json_columns = [column.key for column in inspect(cls).columns
                    if isinstance(column.type, JSON)]
    # Bind the actual stored JSON bytes, not a new serialization. Older rows
    # can use Unicode, compact or indented JSON with the same reviewed value.
    with db.no_autoflush:
        stored = db.execute(select(*(cast(getattr(cls, key), String).label(key)
                                     for key in json_columns)).where(cls.id == original['id'])).mappings().one_or_none()
    if stored is None or any(
            revision_sha256(json.loads(stored[key]) if stored[key] is not None else None)
            != revision_sha256(original[key]) for key in json_columns):
        db.rollback()
        raise HTTPException(409, 'This issue changed during review; no status update was applied.')
    conditions = []
    for key, value in original.items():
        column = getattr(cls, key)
        if key in json_columns:
            conditions.append(cast(column, String) == stored[key])
        else:
            conditions.append(column == value)
    statement = update(cls).where(*conditions)
    with db.no_autoflush:
        result = db.execute(statement.values(**changes).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'This issue changed during review; no status update was applied.')
    db.expire(row)
