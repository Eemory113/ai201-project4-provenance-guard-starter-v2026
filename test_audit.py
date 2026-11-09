"""Contract tests for the JSONL audit logger."""

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import audit


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        log_dir = Path(self.temp_dir.name) / "logs"
        self.log_dir_patch = patch.object(audit.config, "LOG_DIR", log_dir)
        self.log_file_patch = patch.object(audit.config, "AUDIT_LOG", log_dir / "audit.jsonl")
        self.log_dir_patch.start()
        self.log_file_patch.start()
        self.addCleanup(self.log_file_patch.stop)
        self.addCleanup(self.log_dir_patch.stop)

    def assert_valid_timestamp(self, entry):
        timestamp = datetime.fromisoformat(entry["timestamp"])
        self.assertEqual(timestamp.tzinfo, timezone.utc)

    def test_decision_matches_schema_and_round_trips(self):
        entry = audit.log_decision(
            content_id="content-1",
            creator_id="creator-1",
            guess="ai",
            model_score=0.7,
            style_score=0.6,
            combined_score=0.65,
            label="unsure",
        )

        self.assertEqual(
            set(entry),
            {
                "timestamp", "event", "content_id", "creator_id", "guess",
                "model_score", "style_score", "combined_score", "label", "status",
            },
        )
        self.assert_valid_timestamp(entry)
        self.assertEqual(entry["event"], "decision")
        self.assertEqual(entry["status"], "decided")
        self.assertEqual(audit.read_entries(), [entry])

    def test_decision_preserves_additional_fields(self):
        entry = audit.log_decision(
            content_id="content-2",
            creator_id="creator-2",
            request_id="request-1",
        )

        self.assertEqual(entry["request_id"], "request-1")
        self.assertEqual(audit.read_entries(), [entry])

    def test_domain_events_can_be_correlated_to_http_request(self):
        decision = audit.log_decision(
            "content-1", "creator-1", request_id="request-1"
        )
        appeal = audit.log_appeal(
            "content-1", "creator-1", "Please review.", request_id="request-2"
        )
        rejection = audit.log_rejection(
            "creator-1", "rate_limited", request_id="request-3"
        )

        self.assertEqual(decision["request_id"], "request-1")
        self.assertEqual(appeal["request_id"], "request-2")
        self.assertEqual(rejection["request_id"], "request-3")

    def test_appeal_matches_schema_and_round_trips(self):
        entry = audit.log_appeal(
            content_id="content-1",
            creator_id="creator-1",
            reasoning="I wrote this myself.",
        )

        self.assertEqual(
            set(entry),
            {
                "timestamp", "content_id", "creator_id", "event", "status",
                "reasoning", "reasoning_truncated",
            },
        )
        self.assert_valid_timestamp(entry)
        self.assertEqual(entry["event"], "appeal")
        self.assertEqual(entry["status"], "under_review")
        self.assertEqual(audit.read_entries(), [entry])

    def test_decision_and_appeal_content_are_bounded(self):
        long_text = "x" * (audit.config.AUDIT_MAX_CONTENT_CHARS + 1)
        decision = audit.log_decision(
            "content-1", "creator-1", submitted_text=long_text
        )
        appeal = audit.log_appeal("content-1", "creator-1", long_text)

        self.assertEqual(
            len(decision["submitted_text"]), audit.config.AUDIT_MAX_CONTENT_CHARS
        )
        self.assertTrue(decision["submitted_text_truncated"])
        self.assertEqual(
            len(appeal["reasoning"]), audit.config.AUDIT_MAX_CONTENT_CHARS
        )
        self.assertTrue(appeal["reasoning_truncated"])

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_log_files_have_private_permissions(self):
        audit.log_rejection("creator-1", "invalid_input")

        self.assertEqual(stat.S_IMODE(audit.config.LOG_DIR.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(audit.config.AUDIT_LOG.stat().st_mode), 0o600)

    def test_rotation_retains_recent_entries_and_clear_removes_backups(self):
        with patch.object(audit.config, "AUDIT_MAX_BYTES", 350), patch.object(
            audit.config, "AUDIT_BACKUP_COUNT", 2
        ):
            for index in range(10):
                audit.log_rejection(f"creator-{index}", "invalid_input")

            paths = [
                audit.config.AUDIT_LOG.with_name(f"{audit.config.AUDIT_LOG.name}.{index}")
                for index in range(1, audit.config.AUDIT_BACKUP_COUNT + 1)
            ]
            self.assertTrue(any(path.exists() for path in paths))
            self.assertTrue(all(path.stat().st_size <= 350 for path in paths if path.exists()))
            entries = audit.read_entries()
            self.assertLessEqual(len(entries), 9)
            self.assertEqual(entries[-1]["creator_id"], "creator-9")
            self.assertEqual(audit.read_entries(limit=2), entries[-2:])

            count = len(entries)
            self.assertEqual(audit.clear(), count)
            self.assertFalse(any(path.exists() for path in paths))
            self.assertFalse(audit.config.AUDIT_LOG.exists())

    def test_rejection_matches_schema_and_keeps_context(self):
        entry = audit.log_rejection(
            creator_id="creator-1",
            reason="rate_limited",
            limit="10 per minute",
        )

        self.assertEqual(
            set(entry),
            {"timestamp", "creator_id", "event", "status", "reason", "limit"},
        )
        self.assert_valid_timestamp(entry)
        self.assertEqual(entry["event"], "rejected")
        self.assertEqual(entry["status"], "rejected")
        self.assertEqual(audit.read_entries(), [entry])

    def test_reader_skips_malformed_lines_and_applies_limit(self):
        first = audit.log_rejection("creator-1", "invalid_input")
        second = audit.log_rejection("creator-2", "rate_limited")
        with audit.config.AUDIT_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write("{malformed json}\n")
        third = audit.log_rejection("creator-3", "invalid_input")

        self.assertEqual(audit.read_entries(), [first, second, third])
        self.assertEqual(audit.read_entries(limit=2), [second, third])

    def test_entries_for_filters_by_content_id(self):
        decision = audit.log_decision("content-1", "creator-1")
        appeal = audit.log_appeal("content-1", "creator-1", "Please review.")
        audit.log_decision("content-2", "creator-2")

        self.assertEqual(audit.entries_for("content-1"), [decision, appeal])

    def test_clear_removes_entries_and_file(self):
        audit.log_rejection("creator-1", "invalid_input")

        self.assertEqual(audit.clear(), 1)
        self.assertFalse(audit.config.AUDIT_LOG.exists())
        self.assertEqual(audit.read_entries(), [])

    def test_http_request_schema_classifies_response_outcomes(self):
        success = audit.log_http_request(
            "request-1", "GET", "/health", "health", 200, 3.14159
        )
        rejected = audit.log_http_request(
            "request-2", "POST", "/submit", "submit", 429, 2.0
        )
        failed = audit.log_http_request(
            "request-3", "POST", "/submit", "submit", 500, 4.0
        )
        not_found = audit.log_http_request(
            "request-4", "GET", "/missing", None, 404, 1.0
        )

        self.assertEqual(success["event"], "request")
        self.assertEqual(success["outcome"], "success")
        self.assert_valid_timestamp(success)
        self.assertEqual(
            set(success),
            {
                "timestamp", "event", "request_id", "method", "path", "endpoint",
                "status_code", "outcome", "duration_ms",
            },
        )
        self.assertEqual(success["duration_ms"], 3.142)
        self.assertEqual(rejected["outcome"], "rejected")
        self.assertEqual(failed["outcome"], "error")
        self.assertEqual(not_found["outcome"], "rejected")
        self.assertEqual(audit.read_entries(), [success, rejected, failed, not_found])


if __name__ == "__main__":
    unittest.main()
