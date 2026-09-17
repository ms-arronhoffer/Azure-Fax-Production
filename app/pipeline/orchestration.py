"""Replay-safe orchestration: scheduling only, no IO, environment reads or document data."""

from datetime import timedelta

import azure.durable_functions as df


def orchestrate(context):
    fax = context.get_input()["faxId"]
    # Python RetryOptions serializes these two supported properties only.
    retry = df.RetryOptions(5000, 4)

    def task(step):
        return context.call_activity_with_retry("fax_activity", retry, {"faxId": fax, "step": step})

    try:
        result = yield task("prepare")
        if result["state"] == "terminal":
            return {"state": "terminal"}
        phases = ("classify", "type", "extract") if result.get("typeClassifier") else ("classify", "extract")
        for phase in phases:
            result = yield task(phase + "_start")
            if result["state"] == "terminal":
                return {"state": "terminal"}
            # Current orchestration time is recorded in history. Wall-clock sleeps,
            # datetime.now(), random jitter and network calls would break replay.
            for _ in range(240):
                if result["state"] == "succeeded":
                    break
                yield context.create_timer(context.current_utc_datetime + timedelta(seconds=5))
                result = yield task(phase + "_poll")
                if result["state"] == "terminal":
                    return {"state": "terminal"}
            if result["state"] != "succeeded":
                yield task("fail")
                return {"state": "terminal"}
            if phase == "classify":
                result = yield task("remove_covers")
                if result["state"] == "terminal":
                    return {"state": "terminal"}
            if phase == "type":
                result = yield task("decide_type")
                if result["state"] == "terminal":
                    return {"state": "terminal"}
        return (yield task("publish"))
    except Exception:
        # Never interpolate an exception into inputs/custom status; it may quote PHI.
        yield task("fail")
        return {"state": "terminal"}
