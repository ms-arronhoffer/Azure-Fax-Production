from datetime import datetime, timezone
import json
import unittest

from pipeline.orchestration import orchestrate
from tests.test_pipeline import FAX


class Context:
    current_utc_datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def get_input(self):
        return {"faxId": FAX}

    def call_activity_with_retry(self, name, retry, args):
        return {"kind": "activity", "name": name, "retry": retry.to_json(), "args": args}

    def create_timer(self, when):
        self.current_utc_datetime = when
        return {"kind": "timer", "at": when.isoformat()}


class OrchestrationTests(unittest.TestCase):
    def drive(self, outcome):
        context = Context()
        generator, events = orchestrate(context), []
        result = None
        while True:
            try:
                event = generator.send(result)
            except StopIteration as done:
                return events, done.value
            events.append(event)
            result = None if event["kind"] == "timer" else outcome(event["args"]["step"])

    def test_replay_identical_safe_history(self):
        def outcome(step):
            return {"state": "running" if step.endswith("_start") else
                    "succeeded" if step.endswith("_poll") else "completed" if step == "publish" else "ready"}
        first = self.drive(outcome)
        second = self.drive(outcome)
        self.assertEqual(first, second)
        events, result = first
        self.assertEqual(result, {"state": "completed"})
        self.assertEqual(sum(e["kind"] == "timer" for e in events), 2)
        for event in events:
            if event["kind"] == "activity":
                self.assertEqual(set(event["args"]), {"faxId", "step"})
                self.assertEqual(event["retry"], {"firstRetryIntervalInMilliseconds": 5000,
                                                  "maxNumberOfAttempts": 4})

    def test_poll_timeout_is_bounded_and_never_publishes(self):
        events, result = self.drive(lambda step: {"state": "ready" if step == "prepare" else "running"})
        self.assertEqual(sum(e["kind"] == "timer" for e in events), 240)
        steps = [e["args"]["step"] for e in events if e["kind"] == "activity"]
        self.assertEqual(steps[-1], "fail")
        self.assertNotIn("publish", steps)

    def test_two_classifiers_run_in_order_and_replay_identically(self):
        def outcome(step):
            if step == "prepare":
                return {"state": "ready", "typeClassifier": True}
            return {"state": "running" if step.endswith("_start") else
                    "succeeded" if step.endswith("_poll") else "completed" if step == "publish" else "ready"}
        first = self.drive(outcome)
        self.assertEqual(first, self.drive(outcome))
        steps = [e["args"]["step"] for e in first[0] if e["kind"] == "activity"]
        self.assertEqual(steps, ["prepare", "classify_start", "classify_poll", "remove_covers",
                                 "type_start", "type_poll", "decide_type", "extract_start",
                                 "extract_poll", "publish"])

    def test_unknown_type_stops_before_extraction(self):
        def outcome(step):
            if step == "prepare":
                return {"state": "ready", "typeClassifier": True}
            return {"state": "terminal" if step == "decide_type" else
                    "succeeded" if step.endswith(("_start", "_poll")) else "ready"}
        events, result = self.drive(outcome)
        self.assertEqual(result, {"state": "terminal"})
        self.assertNotIn("extract_start", [e["args"]["step"] for e in events if e["kind"] == "activity"])
    def test_failure_history_does_not_include_exception(self):
        generator = orchestrate(Context())
        next(generator)
        recovery = generator.throw(RuntimeError("SECRET PATIENT TEXT"))
        self.assertEqual(recovery["args"]["step"], "fail")
        self.assertNotIn("SECRET", json.dumps(recovery))
        with self.assertRaises(StopIteration):
            generator.send({"state": "terminal"})

    def test_review_short_circuits_all_normal_output(self):
        events, result = self.drive(lambda _: {"state": "terminal"})
        self.assertEqual(len(events), 1)
        self.assertEqual(result, {"state": "terminal"})


if __name__ == "__main__":
    unittest.main()
