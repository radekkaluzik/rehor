"""Regression tests for cycle_runs grouping, progress, and serialization.

RHCLOUD-48536: Verify cycle_runs by-task grouping, pagination, filtering,
progress roundtrip with external_key tasks, and response serialization.
"""

import json
import os

import pytest

from conftest import SCHEMA_PATH

os.environ.setdefault("JIRA_URL", "https://redhat.atlassian.net")

from bot_memory_server.artifacts import JIRA_BASE_URL  # noqa: E402


async def _apply_schema(db):
    schema = SCHEMA_PATH.read_text()
    await db.execute(schema)


async def _insert_task(db, jira_key, status="in_progress", repo="test-repo"):
    row = await db.fetchrow(
        """
        INSERT INTO tasks (jira_key, status, repo, branch,
                           external_key, source_type, source_url, artifacts, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        jira_key,
        status,
        repo,
        f"bot/{jira_key}",
        jira_key,
        "jira",
        f"{JIRA_BASE_URL}/{jira_key}",
        json.dumps([]),
        json.dumps({}),
    )
    return row


async def _insert_cycle_run(
    db,
    task_id=None,
    cycle_type="task_work",
    instance_id=None,
    tool_calls=None,
    tokens_used=None,
    progress=None,
    transcript=None,
):
    row = await db.fetchrow(
        """
        INSERT INTO cycle_runs (task_id, cycle_type, instance_id,
                                tool_calls, tokens_used, progress, transcript)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, task_id, cycle_type, instance_id, started_at, finished_at,
                  tool_calls, tokens_used, progress, created_at,
                  (transcript IS NOT NULL) AS has_transcript
        """,
        task_id,
        cycle_type,
        instance_id,
        tool_calls,
        tokens_used,
        json.dumps(progress or {}),
        transcript,
    )
    return row


# --- Cycle runs by-task grouping ---


@pytest.mark.asyncio
async def test_cycle_runs_by_task_grouping(db):
    await _apply_schema(db)
    task = await _insert_task(db, "RHCLOUD-4000")
    task_id = task["id"]

    await _insert_cycle_run(db, task_id=task_id, tool_calls=50, tokens_used=10000)
    await _insert_cycle_run(
        db, task_id=task_id, tool_calls=30, tokens_used=8000, transcript=b"data"
    )
    await _insert_cycle_run(db, task_id=task_id, tool_calls=20, tokens_used=5000)

    rows = await db.fetch(
        """
        SELECT
            cr.task_id,
            COALESCE(t.jira_key, cr.progress->>'jira_key') AS jira_key,
            t.title,
            t.status::text AS task_status,
            COUNT(*) AS cycle_count,
            COUNT(*) FILTER (WHERE cr.transcript IS NOT NULL) AS transcript_count,
            SUM(cr.tool_calls) AS total_tool_calls,
            SUM(cr.tokens_used) AS total_tokens,
            MIN(cr.started_at) AS first_cycle,
            MAX(cr.started_at) AS last_cycle
        FROM cycle_runs cr
        LEFT JOIN tasks t ON t.id = cr.task_id
        GROUP BY cr.task_id, COALESCE(t.jira_key, cr.progress->>'jira_key'),
                 t.title, t.status
        ORDER BY MAX(cr.started_at) DESC
        """
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["jira_key"] == "RHCLOUD-4000"
    assert r["cycle_count"] == 3
    assert r["transcript_count"] == 1
    assert r["total_tool_calls"] == 100
    assert r["total_tokens"] == 23000
    assert r["first_cycle"] is not None
    assert r["last_cycle"] is not None


# --- Cycle runs by-task with instance filter ---


@pytest.mark.asyncio
async def test_cycle_runs_by_task_instance_filter(db):
    await _apply_schema(db)
    task = await _insert_task(db, "RHCLOUD-4010")
    task_id = task["id"]

    await _insert_cycle_run(db, task_id=task_id, instance_id="bot-1", tool_calls=10)
    await _insert_cycle_run(db, task_id=task_id, instance_id="bot-2", tool_calls=20)

    rows = await db.fetch(
        """
        SELECT cr.task_id, COUNT(*) AS cycle_count, SUM(cr.tool_calls) AS total_tool_calls
        FROM cycle_runs cr
        LEFT JOIN tasks t ON t.id = cr.task_id
        WHERE cr.instance_id = $1
        GROUP BY cr.task_id
        """,
        "bot-1",
    )
    assert len(rows) == 1
    assert rows[0]["cycle_count"] == 1
    assert rows[0]["total_tool_calls"] == 10


# --- Cycle runs by-task orphan grouping ---


@pytest.mark.asyncio
async def test_cycle_runs_orphan_grouping(db):
    """Cycle runs with no task group by progress->>'jira_key' fallback."""
    await _apply_schema(db)

    await _insert_cycle_run(
        db,
        task_id=None,
        progress={"jira_key": "RHCLOUD-4020", "repo": "orphan-repo"},
        tool_calls=15,
    )
    await _insert_cycle_run(
        db,
        task_id=None,
        progress={"jira_key": "RHCLOUD-4020", "repo": "orphan-repo"},
        tool_calls=25,
    )

    rows = await db.fetch(
        """
        SELECT
            cr.task_id,
            COALESCE(t.jira_key, cr.progress->>'jira_key') AS jira_key,
            COALESCE(t.repo, cr.progress->>'repo') AS repo,
            COUNT(*) AS cycle_count,
            SUM(cr.tool_calls) AS total_tool_calls
        FROM cycle_runs cr
        LEFT JOIN tasks t ON t.id = cr.task_id
        GROUP BY cr.task_id, COALESCE(t.jira_key, cr.progress->>'jira_key'),
                 t.title, t.status, COALESCE(t.repo, cr.progress->>'repo')
        """
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["task_id"] is None
    assert r["jira_key"] == "RHCLOUD-4020"
    assert r["repo"] == "orphan-repo"
    assert r["cycle_count"] == 2
    assert r["total_tool_calls"] == 40


# --- Progress roundtrip with external_key task ---


@pytest.mark.asyncio
async def test_progress_roundtrip_external_key(db):
    await _apply_schema(db)
    task = await _insert_task(db, "RHCLOUD-4030")
    task_id = task["id"]

    progress = {
        "last_step": "tests_passing",
        "next_step": "push_and_pr",
        "files_changed": ["src/app.tsx", "src/utils.ts"],
    }
    run = await _insert_cycle_run(db, task_id=task_id, progress=progress, tool_calls=42)

    loaded = await db.fetchrow(
        """
        SELECT cr.*, t.external_key, t.source_type
        FROM cycle_runs cr
        LEFT JOIN tasks t ON t.id = cr.task_id
        WHERE cr.id = $1
        """,
        run["id"],
    )
    assert loaded["external_key"] == "RHCLOUD-4030"
    assert loaded["source_type"] == "jira"

    stored_progress = (
        json.loads(loaded["progress"])
        if isinstance(loaded["progress"], str)
        else loaded["progress"]
    )
    assert stored_progress["last_step"] == "tests_passing"
    assert stored_progress["files_changed"] == ["src/app.tsx", "src/utils.ts"]


# --- Cycle run response serialization ---


@pytest.mark.asyncio
async def test_cycle_run_serialization(db):
    await _apply_schema(db)
    run = await _insert_cycle_run(
        db,
        cycle_type="triage",
        instance_id="bot-test",
        tool_calls=75,
        tokens_used=50000,
        progress={"status": "complete"},
    )

    assert run["id"] is not None
    assert run["cycle_type"] == "triage"
    assert run["instance_id"] == "bot-test"
    assert run["tool_calls"] == 75
    assert run["tokens_used"] == 50000
    assert run["started_at"] is not None
    assert run["created_at"] is not None
    assert run["has_transcript"] is False

    progress = (
        json.loads(run["progress"])
        if isinstance(run["progress"], str)
        else run["progress"]
    )
    assert progress["status"] == "complete"


# --- Cycle run with transcript ---


@pytest.mark.asyncio
async def test_cycle_run_with_transcript_flag(db):
    await _apply_schema(db)
    run = await _insert_cycle_run(db, transcript=b"compressed-transcript-data")
    assert run["has_transcript"] is True


# --- Cycle list pagination ---


@pytest.mark.asyncio
async def test_cycle_runs_pagination(db):
    await _apply_schema(db)
    for _ in range(5):
        await _insert_cycle_run(db)

    total = await db.fetchval("SELECT COUNT(*) FROM cycle_runs")
    assert total == 5

    rows = await db.fetch(
        """
        SELECT id, task_id, cycle_type, instance_id, started_at, finished_at,
               tool_calls, tokens_used, progress, created_at,
               (transcript IS NOT NULL) AS has_transcript
        FROM cycle_runs
        ORDER BY created_at DESC
        LIMIT $1 OFFSET $2
        """,
        2,
        2,
    )
    assert len(rows) == 2


# --- Cycle list filter by cycle_type ---


@pytest.mark.asyncio
async def test_cycle_runs_filter_by_type(db):
    await _apply_schema(db)
    await _insert_cycle_run(db, cycle_type="task_work")
    await _insert_cycle_run(db, cycle_type="triage")
    await _insert_cycle_run(db, cycle_type="triage")

    rows = await db.fetch(
        """
        SELECT id FROM cycle_runs WHERE cycle_type = $1
        """,
        "triage",
    )
    assert len(rows) == 2


# --- Costs POST with external_key ---


@pytest.mark.asyncio
async def test_cost_record_populates_external_key(db):
    await _apply_schema(db)

    row = await db.fetchrow(
        """
        INSERT INTO cycles (label, session_id, num_turns, duration_ms, cost_usd,
                            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                            model, is_error, no_work, jira_key,
                            repo, work_type, summary,
                            external_key, source_type)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        RETURNING *
        """,
        "test",
        "sess",
        5,
        30000,
        0.25,
        50,
        25,
        10,
        5,
        "claude-opus-4",
        False,
        False,
        "RHCLOUD-4050",
        "test-repo",
        "new_ticket",
        "Fixed button",
        "RHCLOUD-4050",
        "jira",
    )
    assert row["external_key"] == "RHCLOUD-4050"
    assert row["source_type"] == "jira"
    assert row["jira_key"] == "RHCLOUD-4050"
