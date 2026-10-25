"""Global daily usage counters in Postgres: question cap and token budget per JST day.

Stored in the DB (not memory) so restarts and redeploys can't reset them, and several app
instances would share them.
"""

from __future__ import annotations

from datetime import date

import psycopg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_daily (
    day           date PRIMARY KEY,
    questions     integer NOT NULL DEFAULT 0,
    input_tokens  bigint  NOT NULL DEFAULT 0,
    output_tokens bigint  NOT NULL DEFAULT 0,
    cost_usd      numeric NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
"""

# One atomic statement: count the question only while under both limits. No row back = refused.
RESERVE_SQL = """
INSERT INTO usage_daily AS u (day, questions) VALUES (%(day)s, 1)
ON CONFLICT (day) DO UPDATE SET questions = u.questions + 1, updated_at = now()
WHERE u.questions < %(cap)s AND u.input_tokens + u.output_tokens < %(budget)s
RETURNING questions;
"""

RECORD_SQL = """
UPDATE usage_daily SET input_tokens = input_tokens + %(inp)s, output_tokens = output_tokens + %(out)s,
    cost_usd = cost_usd + %(cost)s, updated_at = now()
WHERE day = %(day)s;
"""

TOTALS_SQL = "SELECT questions, input_tokens + output_tokens FROM usage_daily WHERE day = %s;"


class PostgresUsage:
    """The app's usage store. Tests inject an in-memory store with the same three methods."""

    def ensure_schema(self, conn: psycopg.Connection) -> None:
        conn.execute(SCHEMA_SQL)
        conn.commit()

    def reserve(self, conn: psycopg.Connection, day: date, cap: int, budget: int) -> str | None:
        """Count one question for `day` if allowed. Returns None, or the reason it was refused
        ("cap" or "budget"). Tokens are only known after the call, so concurrent in-flight
        requests can overshoot the token budget by a few thousand tokens each."""
        if cap <= 0:
            return "cap"
        if budget <= 0:
            return "budget"
        row = conn.execute(RESERVE_SQL, {"day": day, "cap": cap, "budget": budget}).fetchone()
        conn.commit()
        if row is not None:
            return None
        questions, tokens = conn.execute(TOTALS_SQL, (day,)).fetchone()
        return "cap" if questions >= cap else "budget"

    def record(self, conn: psycopg.Connection, day: date, input_tokens: int, output_tokens: int,
               cost_usd: float) -> None:
        conn.execute(RECORD_SQL, {"day": day, "inp": input_tokens, "out": output_tokens, "cost": cost_usd})
        conn.commit()
