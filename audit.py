"""
The audit log.

Every decision your service makes gets written here as one JSON object per
line. Not `print()` — a file you can read back.

The reason is next unit: you run an attack against this service, and **an
attack you can't see in a log is an attack you can't diagnose.** An input that
slipped past your detector leaves no other trace.

All nine fields the unit 8 rubric requires are in the entry shape from the
start, even though unit 7 only fills some of them. That's deliberate — adding
fields later means your early entries and your late ones have different shapes,
and the one you most want to compare is the one from before the change.

    from audit import log_decision, read_entries

    log_decision(content_id=..., creator_id=..., guess="ai", ...)
    entries = read_entries(limit=50)
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_decision(
    content_id: str,
    creator_id: str,
    guess: str | None = None,
    model_score: float | None = None,
    style_score: float | None = None,
    combined_score: float | None = None,
    label: str | None = None,
    status: str = "decided",
    request_id: str | None = None,
    submitted_text: str | None = None,
    **extra,
) -> dict:
    """
    Write one decision to the audit log.

    The nine required fields, all present from unit 7 whether or not you can
    fill them yet:

        timestamp, content_id, creator_id, guess,
        model_score, style_score, combined_score, label, status

    `status` moves over an item's life: `decided` when first judged,
    `under_review` once the writer appeals.

    Anything extra you pass gets written too — useful for `rejected` entries in
    unit 8, where there's no decision but you still want the trace.
    """
    entry = {
        "timestamp": _now(),
        "event": "decision",
        "content_id": content_id,
        "creator_id": creator_id,
        "guess": guess,
        "model_score": model_score,
        "style_score": style_score,
        "combined_score": combined_score,
        "label": label,
        "status": status,
    }
    if request_id is not None:
        entry["request_id"] = request_id
    if submitted_text is not None:
        entry["submitted_text"] = submitted_text[:config.AUDIT_MAX_CONTENT_CHARS]
        entry["submitted_text_truncated"] = (
            len(submitted_text) > config.AUDIT_MAX_CONTENT_CHARS
        )
    entry.update(extra)
    return _append(entry)


def log_appeal(
    content_id: str,
    creator_id: str,
    reasoning: str,
    *,
    request_id: str | None = None,
) -> dict:
    """
    Record an appeal beside the original decision.

    This doesn't overwrite anything. The log is append-only, so the original
    decision stays exactly where it was and the appeal sits after it — which is
    the whole point of an audit log. Someone reading it later can see what was
    decided, that it was challenged, and in what order.
    """
    bounded_reasoning = reasoning[:config.AUDIT_MAX_CONTENT_CHARS]
    entry = {
        "timestamp": _now(),
        "request_id": request_id,
        "content_id": content_id,
        "creator_id": creator_id,
        "event": "appeal",
        "status": "under_review",
        "reasoning": bounded_reasoning,
        "reasoning_truncated": len(reasoning) > config.AUDIT_MAX_CONTENT_CHARS,
    }
    if request_id is None:
        del entry["request_id"]
    return _append(entry)


def log_rejection(creator_id: str, reason: str, **extra) -> dict:
    """
    Record a request that never became a decision — a rate-limit rejection or
    an input your route refused.

    ⚠️ Unit 8 asks for this explicitly, and the reason is worth holding onto:
    **an attack that shows up only as an absence is one you'll never find.** A
    thousand requests that got a 429 and left no trace look exactly like a
    quiet afternoon.
    """
    return _append({
        "timestamp": _now(),
        "creator_id": creator_id,
        "event": "rejected",
        "status": "rejected",
        "reason": reason,
        **extra,
    })


def log_http_request(
    request_id: str,
    method: str,
    path: str,
    endpoint: str | None,
    status_code: int,
    duration_ms: float,
) -> dict:
    """Record the outcome and timing for one completed HTTP request."""
    if status_code < 400:
        outcome = "success"
    elif status_code < 500:
        outcome = "rejected"
    else:
        outcome = "error"

    return _append({
        "timestamp": _now(),
        "event": "request",
        "request_id": request_id,
        "method": method,
        "path": path,
        "endpoint": endpoint,
        "status_code": status_code,
        "outcome": outcome,
        "duration_ms": round(max(duration_ms, 0.0), 3),
    })


def _append(entry: dict) -> dict:
    line = json.dumps(entry, ensure_ascii=False)
    encoded_line = (line + "\n").encode("utf-8")
    with _lock:
        config.LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        config.LOG_DIR.chmod(0o700)
        _rotate_if_needed(len(encoded_line))
        with config.AUDIT_LOG.open("a", encoding="utf-8") as f:
            os.chmod(config.AUDIT_LOG, 0o600)
            f.write(line + "\n")
    return entry


def _rotate_if_needed(next_line_bytes: int) -> None:
    if (
        not config.AUDIT_LOG.exists()
        or config.AUDIT_LOG.stat().st_size + next_line_bytes <= config.AUDIT_MAX_BYTES
    ):
        return

    oldest_backup = config.AUDIT_LOG.with_name(
        f"{config.AUDIT_LOG.name}.{config.AUDIT_BACKUP_COUNT}"
    )
    oldest_backup.unlink(missing_ok=True)

    for index in range(config.AUDIT_BACKUP_COUNT - 1, 0, -1):
        source = config.AUDIT_LOG.with_name(f"{config.AUDIT_LOG.name}.{index}")
        if source.exists():
            source.replace(config.AUDIT_LOG.with_name(f"{config.AUDIT_LOG.name}.{index + 1}"))

    config.AUDIT_LOG.replace(config.AUDIT_LOG.with_name(f"{config.AUDIT_LOG.name}.1"))


def _log_files() -> list[Path]:
    backups = [
        config.AUDIT_LOG.with_name(f"{config.AUDIT_LOG.name}.{index}")
        for index in range(config.AUDIT_BACKUP_COUNT, 0, -1)
    ]
    return [path for path in backups if path.exists()] + (
        [config.AUDIT_LOG] if config.AUDIT_LOG.exists() else []
    )


def read_entries(limit: int | None = None) -> list[dict]:
    """
    Read the log back, oldest first. `limit` keeps the most recent N.

    A malformed line is skipped rather than raising — a log that can't be read
    because of one bad row is worse than a log with a gap.
    """
    if limit is not None and limit <= 0:
        return []

    log_paths = _log_files()
    if not log_paths:
        return []

    entries = []
    for log_path in log_paths:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return entries[-limit:] if limit else entries


def clear() -> int:
    """Wipe the log. Handy between attack runs; returns how many went."""
    count = len(read_entries())
    with _lock:
        for log_path in _log_files():
            log_path.unlink()
    return count


def entries_for(content_id: str) -> list[dict]:
    """Every entry touching one content id, in order."""
    return [e for e in read_entries() if e.get("content_id") == content_id]
