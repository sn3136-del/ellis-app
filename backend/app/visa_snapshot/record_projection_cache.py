"""Bounded reuse of unchanged QC projections during a whole-list rebuild.

This is an optimization of row_projection, never a second records policy.
Inputs include the complete cache row, current disputes, source-store version,
UTC date and staleness. Expiry is no longer than the existing list-cache TTL.
"""
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import time

_LOCK = threading.Lock()
_CACHE = {}
MAX_ROWS = 4096
TTL_SECONDS = 120


def context_version(vo):
    from . import source_authority, scheme_registry
    paths = set(vo.OVERRIDES.parent.glob('*.json'))
    # Changed override entries are compared per route below. An edit to one
    # answer must not discard the checked projections of every other route.
    paths.difference_update([vo.OVERRIDES, vo.operator_overrides_path()])
    # Reviewed-warning reconciliations validate the exact overlay envelope,
    # beyond its parsed per-route entry, so retain their byte-level versions.
    paths.add(vo.OVERRIDES.parent / vo.REVIEWED_OVERLAY_LIST)
    paths.update(vo._reviewed_overlay_paths())
    paths.add(Path(source_authority._providers_path()))
    paths.add(Path(scheme_registry._seed_path()))
    paths.add(Path(os.getenv('ELLIS_DATA_DIR') or str(Path(__file__).resolve().parents[3] / 'data'))
        / 'database_seed' / 'official_portals.json')
    stores = [(str(p), vo._stat_sig(p)) for p in sorted(paths)]
    switches = sorted((k, v) for k, v in os.environ.items() if k.startswith('ELLIS_'))
    return _digest([stores, switches, getattr(vo._table(), 'store_errors', ()),
        datetime.now(timezone.utc).date().isoformat()])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def project(db, row, route, version, builder, *, ttl=TTL_SECONDS):
    from . import kimi_primary, freshness, verified_overrides
    # This one historical repair also reads an audit receipt in another
    # table. Keep it uncached so an audit edit cannot preserve its release.
    if row.cache_key.startswith('KOR|KOR|CHN|') or ttl <= 0:
        return builder(db, row, route)
    identity = _digest([version, route, verified_overrides.find(route),
        {c.name: getattr(row, c.name) for c in row.__table__.columns},
        kimi_primary._is_stale(row), freshness.active_disputed_fields(db, row.cache_key)])
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(row.cache_key)
        if entry and entry[0] == identity and now - entry[1] < min(ttl, TTL_SECONDS):
            return deepcopy(entry[2])
    rows = builder(db, row, route)
    with _LOCK:
        if len(_CACHE) >= MAX_ROWS and row.cache_key not in _CACHE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[row.cache_key] = (identity, now, deepcopy(rows))
    return rows
