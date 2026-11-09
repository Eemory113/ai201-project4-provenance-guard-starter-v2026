"""HTTP request-correlation tests for the Flask application."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import abort

import app as app_module
import audit
import config


def _test_server_error():
    raise RuntimeError("test-only failure")


def _test_rate_limit():
    abort(429, description="test rate limit")


if "_test_server_error" not in app_module.app.view_functions:
    app_module.app.add_url_rule(
        "/_test/server-error",
        endpoint="_test_server_error",
        view_func=_test_server_error,
    )
if "_test_rate_limit" not in app_module.app.view_functions:
    app_module.app.add_url_rule(
        "/_test/rate-limit",
        endpoint="_test_rate_limit",
        view_func=_test_rate_limit,
    )


class RequestAuditTests(unittest.TestCase):
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

        self.previous_testing = app_module.app.testing
        app_module.app.testing = False
        self.addCleanup(setattr, app_module.app, "testing", self.previous_testing)
        self.client = app_module.app.test_client()
        if hasattr(app_module.limiter, "reset"):
            app_module.limiter.reset()

    def request_event_for(self, response):
        request_id = response.headers.get("X-Request-ID")
        self.assertIsNotNone(request_id)
        matches = [
            entry for entry in audit.read_entries()
            if entry.get("event") == "request" and entry.get("request_id") == request_id
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_successful_request_has_matching_response_id_and_event(self):
        response = self.client.post(
            "/ping",
            json={"message": "request body should not be logged"},
            headers={
                "Authorization": "Bearer do-not-log",
                "X-Request-ID": "caller-controlled-id",
            },
        )

        self.assertEqual(response.status_code, 200)
        entry = self.request_event_for(response)
        self.assertNotEqual(response.headers["X-Request-ID"], "caller-controlled-id")
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/ping")
        self.assertEqual(entry["endpoint"], "ping")
        self.assertEqual(entry["status_code"], 200)
        self.assertEqual(entry["outcome"], "success")
        self.assertGreaterEqual(entry["duration_ms"], 0)
        self.assertNotIn("request body should not be logged", str(entry))
        self.assertNotIn("do-not-log", str(entry))

    def test_every_application_route_gets_a_request_event(self):
        requests = [
            ("post", "/ping", {"message": "hello"}),
            ("get", "/health", None),
            ("post", "/submit", {"text": "submission", "creator_id": "creator-1"}),
            ("post", "/appeal", {"content_id": "content-1", "reasoning": "appeal"}),
            ("get", "/log", None),
        ]

        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, json=body)
                self.request_event_for(response)

        request_events = [
            entry for entry in audit.read_entries()
            if entry.get("event") == "request"
        ]
        self.assertEqual(len(request_events), len(requests))

    def test_not_found_request_is_logged_as_rejected(self):
        response = self.client.get("/not-a-route")

        entry = self.request_event_for(response)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(entry["endpoint"])
        self.assertEqual(entry["outcome"], "rejected")

    def test_log_endpoint_denies_non_loopback_and_caps_results(self):
        denied = self.client.get(
            "/log",
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        self.assertEqual(denied.status_code, 403)

        for index in range(config.AUDIT_LOG_MAX_ENTRIES + 5):
            audit.log_rejection(f"creator-{index}", "invalid_input")
        allowed = self.client.get("/log?limit=10000")

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json["count"], config.AUDIT_LOG_MAX_ENTRIES)
        self.assertEqual(len(allowed.json["entries"]), config.AUDIT_LOG_MAX_ENTRIES)

    def test_log_endpoint_rejects_non_positive_limits(self):
        for limit in ("0", "-1", "not-a-number"):
            with self.subTest(limit=limit):
                response = self.client.get(f"/log?limit={limit}")
                self.assertEqual(response.status_code, 400)

    def test_debug_is_opt_in_and_loopback_only(self):
        self.assertFalse(config.debug_enabled("127.0.0.1", "0"))
        self.assertTrue(config.debug_enabled("localhost", "1"))
        self.assertTrue(config.debug_enabled("::1", "1"))
        self.assertFalse(config.debug_enabled("0.0.0.0", "1"))
        self.assertFalse(config.debug_enabled("192.0.2.1", "1"))

    def test_builtin_server_refuses_non_loopback_bind(self):
        with patch.object(config, "HOST", "0.0.0.0"), patch.object(
            app_module.app, "run"
        ) as run_server:
            with self.assertRaisesRegex(SystemExit, "production WSGI server"):
                app_module.main()
        run_server.assert_not_called()

    def test_rate_limit_rejection_and_request_event_share_id(self):
        response = self.client.get("/_test/rate-limit")
        request_id = response.headers["X-Request-ID"]
        entries = audit.read_entries()

        rejection = next(entry for entry in entries if entry.get("event") == "rejected")
        request_event = next(
            entry for entry in entries
            if entry.get("event") == "request" and entry.get("request_id") == request_id
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(rejection["request_id"], request_id)
        self.assertEqual(rejection["reason"], "rate_limited")
        self.assertEqual(request_event["outcome"], "rejected")

    def test_server_error_request_is_logged(self):
        with self.assertLogs(app_module.app.logger, level="ERROR"):
            response = self.client.get("/_test/server-error")

        entry = self.request_event_for(response)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(entry["status_code"], 500)
        self.assertEqual(entry["outcome"], "error")

    def test_reraised_exception_is_logged_during_teardown(self):
        app_module.app.testing = True
        with self.assertRaisesRegex(RuntimeError, "test-only failure"):
            self.client.get("/_test/server-error")

        request_events = [
            entry for entry in audit.read_entries()
            if entry.get("event") == "request"
        ]
        self.assertEqual(len(request_events), 1)
        self.assertEqual(request_events[0]["status_code"], 500)
        self.assertEqual(request_events[0]["outcome"], "error")

    @unittest.skipUnless(config.RATE_LIMITING_ENABLED, "rate limiting is disabled")
    def test_submit_rate_limit_counts_by_address_not_creator_id(self):
        responses = [
            self.client.post(
                "/submit",
                json={"text": "test input", "creator_id": f"creator-{index}"},
            )
            for index in range(config.RATE_LIMIT_PER_MINUTE + 1)
        ]

        self.assertEqual(
            [response.status_code for response in responses[:-1]],
            [501] * config.RATE_LIMIT_PER_MINUTE,
        )
        limited_response = responses[-1]
        self.assertEqual(limited_response.status_code, 429)
        request_id = limited_response.headers["X-Request-ID"]
        entries = audit.read_entries()
        rejection = next(
            entry for entry in entries
            if entry.get("event") == "rejected" and entry.get("request_id") == request_id
        )
        request_event = next(
            entry for entry in entries
            if entry.get("event") == "request" and entry.get("request_id") == request_id
        )
        self.assertEqual(rejection["reason"], "rate_limited")
        self.assertEqual(request_event["status_code"], 429)
        another_address = self.client.post(
            "/submit",
            json={"text": "test input", "creator_id": "creator-0"},
            environ_base={"REMOTE_ADDR": "192.0.2.20"},
        )
        self.assertEqual(another_address.status_code, 501)


if __name__ == "__main__":
    unittest.main()
