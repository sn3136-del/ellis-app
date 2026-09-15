"""Durable paid-attempt ceiling for the automatic refresh process only.

A reservation is committed before HTTP. Missing/late usage never releases it;
restarts and concurrent workers share the same six-hour ledger window. This is
separate from the best-effort diagnostic usage log. No prompts are persisted.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

LIMIT_NUSD = 9_000_000_000
WINDOW_SECONDS = 6 * 60 * 60
INPUT_RATE = 3_000       # $3 per million tokens, in nanodollars per token
CACHED_RATE = 300        # $0.30 per million
OUTPUT_RATE = 15_000     # $15 per million, including reasoning output
_ACTIVE = ContextVar('refresh_cost_budget', default=None)


class RefreshBudgetExceeded(RuntimeError):
    """A paid attempt was not sent; free source reads may continue."""


@contextmanager
def _connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute('PRAGMA synchronous=FULL')
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class Budget:
    def __init__(self, path, cycle_id):
        self.path, self.cycle_id = Path(path), cycle_id

    def reserve(self, body):
        # Only bounded, single-choice K3 text requests have a known price and
        # upper bound here. Do not silently change a configured model.
        messages = body.get('messages')
        maximum = body.get('max_tokens')
        if (body.get('model') != 'kimi-k3' or type(maximum) is not int or maximum <= 0
                or body.get('n', 1) != 1 or body.get('stream') or body.get('tools')
                or not isinstance(messages, list) or not messages
                or any(not isinstance(m, dict) or not isinstance(m.get('content'), str) for m in messages)):
            raise RefreshBudgetExceeded('refresh request has no supported cost bound')
        # UTF-8 bytes are a conservative text token bound. The entire request
        # serialization plus 4096 tokens covers message/framing overhead; no
        # cache-hit discount is assumed until actual usage is available.
        input_bound = len(json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')) + 4096
        amount = input_bound * INPUT_RATE + maximum * OUTPUT_RATE
        attempt = uuid4().hex
        try:
            with _connect(self.path) as db:
                row = db.execute('SELECT spent, blocked FROM cycles WHERE id=?', (self.cycle_id,)).fetchone()
                if row is None or row[1] or row[0] + amount > LIMIT_NUSD:
                    raise RefreshBudgetExceeded('refresh cycle paid budget exhausted')
                db.execute('UPDATE cycles SET spent=spent+? WHERE id=?', (amount, self.cycle_id))
                db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                           (attempt, self.cycle_id, amount, input_bound, maximum, 'reserved'))
            return attempt
        except (OSError, sqlite3.Error) as exc:
            raise RefreshBudgetExceeded('refresh budget ledger unavailable') from exc

    def settle(self, attempt, usage, response_model=None):
        """Only complete, self-consistent actual usage can reduce a reserve."""
        if not isinstance(usage, dict):
            return
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if any(type(n) is not int or n < 0 for n in (prompt, completion)):
            return
        if response_model not in (None, 'kimi-k3'):
            return
        cached = usage.get('cached_prompt_tokens', usage.get('cached_tokens', 0))
        if type(cached) is not int or not 0 <= cached <= prompt:
            return
        if 'total_tokens' in usage and usage['total_tokens'] != prompt + completion:
            return
        actual = (prompt-cached)*INPUT_RATE + cached*CACHED_RATE + completion*OUTPUT_RATE
        try:
            with _connect(self.path) as db:
                row = db.execute('SELECT amount,input_bound,output_bound,state FROM attempts WHERE id=? AND cycle=?',
                                 (attempt, self.cycle_id)).fetchone()
                if row is None or row[3] != 'reserved':
                    return  # idempotent settlement; never refund twice
                if prompt > row[1] or completion > row[2] or actual > row[0]:
                    # Provider contract/pricing mismatch: retain the reservation
                    # and stop all later paid attempts; expose the breach.
                    db.execute('UPDATE cycles SET blocked=1 WHERE id=?', (self.cycle_id,))
                    db.execute("UPDATE attempts SET state='usage_bound_breach' WHERE id=?", (attempt,))
                    return
                db.execute('UPDATE cycles SET spent=spent-? WHERE id=?', (row[0]-actual, self.cycle_id))
                db.execute("UPDATE attempts SET amount=?,state='settled' WHERE id=?", (actual, attempt))
        except (OSError, sqlite3.Error):
            # The durable full reservation remains charged on I/O failure.
            return

    def snapshot(self):
        with _connect(self.path) as db:
            row = db.execute('SELECT started,spent,blocked FROM cycles WHERE id=?', (self.cycle_id,)).fetchone()
            if row is None:
                raise RefreshBudgetExceeded('refresh budget cycle missing')
            states = dict(db.execute('SELECT state,count(*) FROM attempts WHERE cycle=? GROUP BY state', (self.cycle_id,)))
        return {'cycle_id': self.cycle_id, 'started_at': datetime.fromtimestamp(row[0], timezone.utc).isoformat(),
                'limit_usd': 9, 'charged_or_reserved_usd': row[1]/1e9,
                'remaining_usd': max(0, LIMIT_NUSD-row[1])/1e9,
                'settled_attempts': states.get('settled', 0),
                'unknown_usage_attempts': states.get('reserved', 0),
                'blocked': bool(row[2]), 'pricing': 'kimi-k3:3/0.30/15 USD per million input/cached/output'}


def open_cycle(path, *, now=None, prior_cycle=None, legacy_started_at=None):
    """Reuse a durable window for six hours, even after a completed/restarted run.

    A deployment into an old unmetered cycle fails closed on its unknown spend.
    The original window is not extended or shortened by restarting the worker.
    """
    stamp = (now or datetime.now(timezone.utc)).timestamp()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    with _connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS cycles (id TEXT PRIMARY KEY, started REAL NOT NULL, spent INTEGER NOT NULL, blocked INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, cycle TEXT NOT NULL, amount INTEGER NOT NULL, input_bound INTEGER NOT NULL, output_bound INTEGER NOT NULL, state TEXT NOT NULL)')
        if prior_cycle and not db.execute('SELECT 1 FROM cycles WHERE id=?', (prior_cycle,)).fetchone():
            raise RefreshBudgetExceeded('persisted refresh budget cycle missing')
        row = db.execute('SELECT id,started FROM cycles ORDER BY started DESC LIMIT 1').fetchone()
        if row and stamp < row[1] + WINDOW_SECONDS:
            return Budget(path, row[0])
        cycle_id, started, spent = uuid4().hex, stamp, 0
        if row is None and legacy_started_at:
            legacy = datetime.fromisoformat(str(legacy_started_at).replace('Z', '+00:00'))
            if legacy.tzinfo is None:
                legacy = legacy.replace(tzinfo=timezone.utc)
            if stamp < legacy.timestamp() + WINDOW_SECONDS:
                started, spent = legacy.timestamp(), LIMIT_NUSD
        db.execute('INSERT INTO cycles VALUES (?,?,?,0)', (cycle_id, started, spent))
    path.chmod(0o600)
    return Budget(path, cycle_id)


@contextmanager
def bind(budget):
    token = _ACTIVE.set(budget)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active():
    return _ACTIVE.get()
