import asyncio
import inspect
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import azure.functions as func
import azure.durable_functions as df

import function_app
from pipeline.policy import Actor, fax_id
from pipeline.engine import Engine
from tests.test_pipeline import FAX, FakeAI, MemoryStore, pdf
from tests.test_policy import TENANT, OID, config, principal


def user_function(builder):
    return builder._function.get_user_function()


class APITests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.headers, self.env = principal()
        self.env["PROCESSING_ENABLED"] = "true"
        self.env["MAX_UPLOAD_BYTES"] = "20971520"
        self.env["MAX_PAGES"] = "100"
        self.headers.update({"content-type": "application/pdf", "x-fax-source": "clinic-a",
                             "idempotency-key": FAX})
        self.submit = user_function(function_app.submit_fax)
        self.get = user_function(function_app.get_fax)
        self.patches = [
            patch.dict(os.environ, self.env, clear=True),
            patch.object(function_app, "services", return_value=(self.store, None)),
            patch.object(function_app, "admission_config", side_effect=lambda: config()),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def request(self, body=None, headers=None):
        return func.HttpRequest("POST", "https://private.example/api/faxes",
                                headers=headers or self.headers,
                                body=pdf() if body is None else body)

    def status_request(self, fax, headers=None):
        return func.HttpRequest("GET", f"https://private.example/api/faxes/{fax}",
                                headers=headers or self.headers, body=b"",
                                route_params={"faxId": fax})

    def test_submit_duplicate_and_payload_conflict(self):
        req = self.request()
        first, second = self.submit(req), self.submit(req)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.get_body(), second.get_body())
        self.assertEqual(self.submit(self.request(body=pdf(3))).status_code, 409)
        self.assertTrue(json.loads(first.get_body())["statusUrl"].startswith("/api/faxes/"))

    def test_auth_role_source_and_disabled(self):
        no_roles, _ = principal(roles=())
        self.assertEqual(self.submit(self.request(headers={**self.headers, **no_roles})).status_code, 403)
        self.assertEqual(self.submit(self.request(headers={**self.headers, "x-fax-source": "other"})).status_code, 403)
        with patch.dict(os.environ, {"PROCESSING_ENABLED": "false"}):
            self.assertEqual(self.submit(self.request()).status_code, 503)
        with patch.dict(os.environ, {"WEBSITE_AUTH_ENABLED": "false"}):
            self.assertEqual(self.submit(self.request()).status_code, 403)

    def test_input_limits_and_invalid_id(self):
        self.assertEqual(self.submit(self.request(body=b"")).status_code, 413)
        self.assertEqual(self.submit(self.request(body=b"not-pdf")).status_code, 400)
        self.assertEqual(self.submit(self.request(headers={**self.headers, "idempotency-key": "no"})).status_code, 400)
        with patch.dict(os.environ, {"MAX_UPLOAD_BYTES": "2"}):
            self.assertEqual(self.submit(self.request()).status_code, 413)

    def test_ownership_and_admin_status(self):
        fax = json.loads(self.submit(self.request()).get_body())["faxId"]
        self.assertEqual(self.get(self.status_request(fax)).status_code, 200)
        stranger = Actor(TENANT, FAX, frozenset({"Fax.Submit"}))
        with patch.object(function_app, "authenticate", return_value=stranger):
            self.assertEqual(self.get(self.status_request(fax)).status_code, 404)
        admin = Actor(TENANT, FAX, frozenset({"Fax.Admin"}))
        with patch.object(function_app, "authenticate", return_value=admin):
            result = self.get(self.status_request(fax))
            self.assertEqual(result.status_code, 200)
            self.assertNotIn("owner", json.loads(result.get_body()))

    def test_enqueue_fail_keeps_original_and_same_id(self):
        self.store.enqueue_failure = True
        self.assertEqual(self.submit(self.request()).status_code, 503)
        self.assertEqual(len(self.store.jobs), 1)
        self.store.enqueue_failure = False
        self.assertEqual(self.submit(self.request()).status_code, 202)
        self.assertEqual(len(self.store.jobs), 1)

    def test_duplicate_queue_does_not_restart_active_instance(self):
        fax = json.loads(self.submit(self.request()).get_body())["faxId"]
        client = AsyncMock()
        client.get_status.return_value = None
        message = func.QueueMessage(body=json.dumps({"faxId": fax}))
        starter = inspect.unwrap(user_function(function_app.queue_starter))
        asyncio.run(starter(message, client))
        client.start_new.assert_awaited_once()
        # Durable's asynchronous start may not immediately appear in get_status.
        asyncio.run(starter(message, client))
        client.start_new.assert_awaited_once()
        client.get_status.return_value = type("Status", (), {"runtime_status": df.OrchestrationRuntimeStatus.Running})()
        asyncio.run(starter(message, client))
        client.start_new.assert_awaited_once()

    def test_poison_processing_records_failure_review(self):
        fax = json.loads(self.submit(self.request()).get_body())["faxId"]
        engine = Engine(self.store, FakeAI())
        handler = inspect.unwrap(user_function(function_app.poison_handler))
        client = AsyncMock()
        client.get_status.return_value = None
        with patch.object(function_app, "services", return_value=(self.store, engine)):
            asyncio.run(handler(func.QueueMessage(body=json.dumps({"faxId": fax})), client))
        self.assertEqual(self.store.job(fax)["status"], "Failed")
        self.assertEqual(self.store.job(fax)["reason"], "queue_poison")
        self.assertTrue(self.store.exists("human-review", f"{fax}/original"))
        self.assertFalse(self.store.exists("extracted", f"{fax}/manifest.json"))

    def test_poison_duplicate_preserves_active_or_unconfirmed_job(self):
        fax = json.loads(self.submit(self.request()).get_body())["faxId"]
        engine = Engine(self.store, FakeAI())
        handler = inspect.unwrap(user_function(function_app.poison_handler))
        client = AsyncMock()
        message = func.QueueMessage(body=json.dumps({"faxId": fax}))
        with patch.object(function_app, "services", return_value=(self.store, engine)):
            client.get_status.return_value = type(
                "Status", (), {"runtime_status": df.OrchestrationRuntimeStatus.Running})()
            asyncio.run(handler(message, client))
            client.get_status.return_value = None
            self.store.jobs[fax]["orchestrationIntentAt"] = "2026-09-08T20:00:00+00:00"
            asyncio.run(handler(message, client))
        self.assertEqual(self.store.job(fax)["status"], "Accepted")
        self.assertFalse(self.store.exists("human-review", f"{fax}/review.json"))


if __name__ == "__main__":
    unittest.main()
