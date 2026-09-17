import base64
import copy
import json
import os
import unittest
from unittest.mock import patch

from pipeline.policy import (
    Actor, Rejected, authenticate, fax_id, gate, matching_routes, page_plan,
    permitted_source, validate_config,
)
from pipeline.intelligence import Intelligence, validate_schema_path
from pipeline.storage import credential, endpoint

TENANT = "11111111-1111-4111-8111-111111111111"
OID = "22222222-2222-4222-8222-222222222222"
OWNER = f"{TENANT}:{OID}"


def config():
    # Only mock service IDs. Production config intentionally contains no model IDs.
    return {"enabled": True, "example": False,
            "classifier": {"id": "mock-classifier", "minimumConfidence": .8,
                           "coverClasses": ["cover"], "bodyClasses": {"body": "invoice"}},
            "documentTypes": {"invoice": {"modelId": "mock-model", "resultDocType": "invoice",
                                         "expectedFields": ["Id", "Amount"]}},
            "sources": {OWNER: ["clinic-a"]},
            "routes": [{"id": "archive", "match": {"source": "clinic-a"}},
                       {"id": "billing", "match": {"documentType": "invoice", "fieldEquals": {"Amount": 0}}}]}


def principal(roles=("Fax.Submit",)):
    env = {"WEBSITE_AUTH_ENABLED": "true", "ENTRA_TENANT_ID": TENANT,
           "ENTRA_API_AUDIENCE": "api://fax"}
    claims = [{"typ": k, "val": v} for k, v in
              [("tid", TENANT), ("oid", OID), ("iss", f"https://login.microsoftonline.com/{TENANT}/v2.0"),
               ("aud", "api://fax")] + [("roles", r) for r in roles]]
    value = base64.b64encode(json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()
    return {"x-ms-client-principal": value}, env


class IdentityTests(unittest.TestCase):
    @patch("pipeline.storage.ManagedIdentityCredential")
    def test_function_runtime_selects_configured_user_assigned_identity(self, managed):
        with patch.dict(os.environ, {
                "WEBSITE_INSTANCE_ID": "instance",
                "AZURE_CLIENT_ID": "33333333-3333-4333-8333-333333333333",
        }, clear=True):
            credential()
        managed.assert_called_once_with(client_id="33333333-3333-4333-8333-333333333333")


class ConfidenceTests(unittest.TestCase):
    def test_minimum_confidence_and_reasons(self):
        fields = {"Id": {"valueString": "ok", "confidence": .99},
                  "Amount": {"valueNumber": 0, "confidence": .7999}}
        decision = gate(fields, ["Id", "Amount"])
        self.assertEqual(decision["minimumConfidence"], .7999)
        self.assertEqual(decision["reasons"]["Amount"], [{"index": 0, "code": "below_threshold"}])
        decision = gate(fields, ["Id", "Missing"])
        self.assertIsNone(decision["minimumConfidence"])
        self.assertEqual(decision["reasons"]["Missing"], [{"code": "missing_field"}])
        decision = gate({"Id": {"valueString": "ok"}}, ["Id"])
        self.assertIsNone(decision["minimumConfidence"])
        self.assertEqual(decision["reasons"]["Id"][0]["code"], "missing_confidence")

    def test_malformed_array_member_rejects(self):
        self.assertFalse(gate({"Items": {"valueArray": ["not-a-field"]}},
                              ["Items[*].Name"])["passed"])

    def test_exact_boundary(self):
        for score, expected in [(.7999, False), (.8, True), (None, False),
                                (True, False), (float("nan"), False), (1.01, False)]:
            with self.subTest(score=score):
                self.assertEqual(gate({"Id": {"valueString": "x", "confidence": score}}, ["Id"])["passed"], expected)

    def test_every_field_not_average(self):
        fields = {"Id": {"valueString": "ok", "confidence": 1},
                  "Amount": {"valueNumber": 0, "confidence": .7999}}
        self.assertFalse(gate(fields, ["Id", "Amount"])["passed"])
        self.assertFalse(gate(fields, ["Missing"])["passed"])
        del fields["Id"]["confidence"]
        self.assertFalse(gate(fields, ["Id"])["passed"])

    def test_false_zero_valid(self):
        self.assertTrue(gate({"A": {"valueBoolean": False, "confidence": .8},
                              "B": {"valueNumber": 0, "confidence": .8}}, ["A", "B"])["passed"])

    def test_absent_unreadable_blank(self):
        for value in (None, "", " ", "unreadable", "illegible", "?", "unknown"):
            self.assertFalse(gate({"A": {"valueString": value, "confidence": 1}}, ["A"])["passed"])

    def test_nested_arrays_all_items(self):
        item = {"valueObject": {"Name": {"valueString": "yes", "confidence": .8}}}
        fields = {"Items": {"valueArray": [item, copy.deepcopy(item)]}}
        self.assertTrue(gate(fields, ["Items[*].Name"])["passed"])
        fields["Items"]["valueArray"][1]["valueObject"]["Name"]["confidence"] = .7999
        self.assertFalse(gate(fields, ["Items[*].Name"])["passed"])
        fields["Items"]["valueArray"] = []
        self.assertFalse(gate(fields, ["Items[*].Name"])["passed"])

    def test_nested_array_inside_object_array(self):
        fields = {"Orders": {"valueArray": [{"valueObject": {"Lines": {
            "valueArray": [{"valueObject": {"Count": {"valueNumber": 0, "confidence": .8}}}]
        }}}]}}
        self.assertTrue(gate(fields, ["Orders[*].Lines[*].Count"])["passed"])
        self.assertFalse(gate(fields, ["Orders[*].Lines[*].Missing"])["passed"])

    def test_currency_requires_actual_amount(self):
        self.assertFalse(gate({"Total": {"valueCurrency": {"currencyCode": "USD"},
                                        "confidence": .9}}, ["Total"])["passed"])
        self.assertTrue(gate({"Total": {"valueCurrency": {"amount": 0, "currencyCode": "USD"},
                                       "confidence": .8}}, ["Total"])["passed"])

    def test_invalid_schema(self):
        for expected in ([], None, ["Items..Name"], [""]):
            with self.assertRaises(Rejected):
                gate({}, expected)


class PolicyTests(unittest.TestCase):
    def test_arm_service_endpoints_accept_only_root_slash(self):
        for service, host in (("blob", "account.blob.core.windows.net"),
                              ("queue", "account.queue.core.windows.net"),
                              ("di", "account.cognitiveservices.azure.com")):
            origin = "https://" + host
            self.assertEqual(endpoint(origin, service), origin)
            self.assertEqual(endpoint(origin + "/", service), origin)
            for invalid in (origin + "//", origin + "/path", origin + "?secret=1",
                            "http://" + host, "https://user@" + host, None):
                with self.subTest(value=invalid), self.assertRaises(Rejected):
                    endpoint(invalid, service)

    def test_cover_and_no_cover(self):
        c = config()["classifier"]
        def doc(page, label):
            return {"docType": label, "confidence": .8, "boundingRegions": [{"pageNumber": page}]}
        plan = page_plan({"documents": [doc(1, "cover"), doc(2, "body")]}, 2, c)
        self.assertEqual(plan["bodyPages"], [2])
        self.assertEqual(page_plan({"documents": [doc(1, "body")]}, 1, c)["coverPages"], [])
        for docs in ([doc(1, "cover")], [], [doc(1, "body"), doc(1, "body")]):
            with self.assertRaises(Rejected):
                page_plan({"documents": docs}, 1, c)

    def test_low_confidence_and_mixed_body_are_ambiguous(self):
        c = config()["classifier"]
        c["bodyClasses"]["other-body"] = "other"
        def doc(page, label, confidence):
            return {"docType": label, "confidence": confidence, "boundingRegions": [{"pageNumber": page}]}
        for docs, count in [([doc(1, "body", .7999)], 1),
                            ([doc(1, "body", .99), doc(2, "other-body", .99)], 2),
                            ([doc(1, "unknown", .99)], 1)]:
            with self.assertRaises(Rejected):
                page_plan({"documents": docs}, count, c)

    def test_fanout_zero_and_many(self):
        c = config()
        self.assertEqual(matching_routes(c, "clinic-a", "invoice", "", {"Amount": [0]}), ["archive", "billing"])
        self.assertEqual(matching_routes(c, "other", "other", "", {}), [])
        c["routes"][0]["match"]["contentContains"] = "needle"
        self.assertEqual(matching_routes(c, "clinic-a", "invoice", "no", {"Amount": [0]}), ["billing"])

    def test_config_fail_closed(self):
        validate_config(config())
        for mutation in ("disabled", "empty", "unsafe-route", "empty-sources", "unguarded-field"):
            c = config()
            if mutation == "disabled": c["enabled"] = False
            if mutation == "empty": c["documentTypes"]["invoice"]["expectedFields"] = []
            if mutation == "unsafe-route": c["routes"][0]["id"] = "../escape"
            if mutation == "empty-sources": c["sources"] = {}
            if mutation == "unguarded-field": c["routes"][1]["match"]["fieldEquals"] = {"Other": "x"}
            with self.subTest(mutation=mutation), self.assertRaises(Rejected):
                validate_config(c)

    def test_auth_platform_issuer_roles_and_source(self):
        headers, env = principal()
        actor = authenticate(headers, env)
        self.assertIn("Fax.Submit", actor.roles)
        self.assertEqual(permitted_source(config(), actor, "clinic-a"), "clinic-a")
        with self.assertRaises(PermissionError):
            permitted_source(config(), actor, "untrusted")
        for change in ({"WEBSITE_AUTH_ENABLED": "false"}, {"ENTRA_TENANT_ID": "other"},
                       {"ENTRA_API_AUDIENCE": "other"}):
            with self.assertRaises(PermissionError):
                authenticate(headers, {**env, **change})
        with self.assertRaises(PermissionError):
            authenticate({"x-ms-client-principal": "fabricated"}, env)

    def test_idempotency_owner_scope(self):
        actor = Actor(TENANT, OID, frozenset())
        key = "33333333-3333-4333-8333-333333333333"
        self.assertEqual(fax_id(actor, key), fax_id(actor, key.upper()))
        self.assertNotEqual(fax_id(actor, key), fax_id(Actor(TENANT, key, frozenset()), key))

    def test_operation_url_rejects_exfiltration(self):
        ai = Intelligence("https://example.cognitiveservices.azure.com", None)
        path = "/documentintelligence/documentModels/mock-model/analyzeResults/abc?api-version=2024-11-30"
        ai.operation_url(ai.endpoint + path, "documentModels", "mock-model")
        for url in ("https://evil.example" + path, ai.endpoint + path + "&extra=1",
                    ai.endpoint + path + "#secret", ai.endpoint + ":443" + path,
                    ai.endpoint + path.replace("mock-model", "other"),
                    ai.endpoint + path.replace("abc?", "../abc?")):
            with self.assertRaises(Rejected):
                ai.operation_url(url, "documentModels", "mock-model")

    def test_live_nested_schema(self):
        schema = {"Items": {"type": "array", "items": {"type": "object",
                  "properties": {"Amount": {"type": "number"}}}}}
        validate_schema_path(schema, "Items[*].Amount")
        with self.assertRaises(Rejected):
            validate_schema_path(schema, "Items[*].Missing")


if __name__ == "__main__":
    unittest.main()
