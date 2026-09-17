"""Azure Functions v2 entry points. EasyAuth is a mandatory deployment boundary."""

from datetime import datetime, timedelta, timezone
import json
import hashlib
import logging
import os
import re
import traceback
from urllib.parse import unquote, urlsplit

import azure.durable_functions as df
import azure.functions as func
from azure.core.exceptions import ResourceNotFoundError

from pipeline.documents import inspect_type
from pipeline.ingestion import ingest, record_rejection, strict_json, MAX_MANIFEST_BYTES
from pipeline.orchestration import orchestrate
from pipeline.policy import Conflict, Rejected, authenticate, fax_id, fingerprint, permitted_source
from pipeline.runtime import admission_config, limits, processing_enabled, services
from pipeline.storage import TERMINAL, checked_id, now
from pipeline.reports import facts, review_etag

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def log_stage_failure(stage, error):
    frames = [{"file": os.path.basename(frame.filename), "line": frame.lineno}
              for frame in traceback.extract_tb(error.__traceback__)[-3:]]
    code = str(error) if isinstance(error, (Rejected, Conflict)) or re.fullmatch(
        r"document_service_http_\d{3}", str(error)) else None
    logging.error("FAX_STAGE_FAILURE %s", json.dumps({
        "stage": stage, "errorType": type(error).__name__, "code": code, "frames": frames}))


def response(value, status=200, headers=None):
    return func.HttpResponse(json.dumps(value, allow_nan=False), status_code=status,
                             mimetype="application/json",
                             headers={"Cache-Control": "no-store", **(headers or {})})


@app.route(route="faxes", methods=["POST"])
def submit_fax(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        if "Fax.Submit" not in actor.roles:
            raise PermissionError()
        if not processing_enabled():
            return response({"error": "processing_disabled"}, 503)
        try:
            config = admission_config()
        except Exception as error:
            log_stage_failure("http_admission_configuration", error)
            logging.error("fax_admission_configuration_unavailable")
            return response({"error": "processing_configuration_unavailable"}, 503)
        source = permitted_source(config, actor, req.headers.get("x-fax-source", ""))
        fax = fax_id(actor, req.headers.get("idempotency-key"))
        mime = req.headers.get("content-type", "").split(";")[0].strip().lower()
        body = req.get_body()
        if not body or len(body) > limits()[0]:
            return response({"error": "invalid_upload_size"}, 413)
        inspect_type(body, mime)
        metadata = {"source": source, "mime": mime, "owner": actor.key,
                    "fingerprint": fingerprint(body, source, mime, actor.key),
                    "provenance": {"channel": "authenticated-http",
                                   "authenticatedActor": actor.key,
                                   "originalSha256": hashlib.sha256(body).hexdigest(),
                                   "identityMeaning": "authenticated submitting identity; not asserted fax sender"}}
        services()[0].intake(fax, body, metadata, config)
        # Relative URLs avoid Host-header injection and never expose Durable management keys.
        status_url = f"/api/faxes/{fax}"
        return response({"faxId": fax, "statusUrl": status_url}, 202,
                        {"Location": status_url, "Retry-After": "10"})
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except Conflict:
        return response({"error": "idempotency_payload_mismatch"}, 409)
    except Rejected as error:
        return response({"error": str(error)}, 400)
    except Exception:
        logging.error("fax_submission_temporarily_unavailable")
        return response({"error": "temporarily_unavailable_retry_same_key"}, 503,
                        {"Retry-After": "30"})


@app.route(route="faxes/{faxId}", methods=["GET"])
def get_fax(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        fax = checked_id(req.route_params["faxId"])
        job = services()[0].job(fax)
        if actor.key != job["owner"] and not actor.roles.intersection({"Fax.Admin", "Fax.Review"}):
            return response({"error": "not_found"}, 404)
        result = {key: job[key] for key in ("faxId", "status", "createdAt", "updatedAt")}
        for key in ("reason", "manifest", "publication", "completionMode", "reviewRevision"):
            if key in job:
                result[key] = job[key]
        result["provenance"] = {"channel": job.get("provenance", {}).get("channel", "authenticated-http"),
                                "identityMeaning": job.get("provenance", {}).get(
                                    "identityMeaning", "authenticated submitting identity")}
        # No raw fields, source provenance, fingerprints or configuration are returned.
        return response(result)
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_status_temporarily_unavailable")
        return response({"error": "temporarily_unavailable"}, 503)


@app.event_grid_trigger(arg_name="event")
@app.durable_client_input(client_name="client")
async def ready_manifest(event: func.EventGridEvent, client):
    # Event Grid is the primary handoff. The subscription and this handler both
    # restrict delivery to BlobCreated events in the ready container.
    if not processing_enabled():
        raise RuntimeError("processing_disabled")
    try:
        if event.event_type != "Microsoft.Storage.BlobCreated":
            raise Rejected("invalid_event_type")
        match = re.fullmatch(
            r"/blobServices/default/containers/ready/blobs/([0-9a-f-]{36}\.json)",
            unquote(event.subject),
        )
        if match is None:
            raise Rejected("invalid_event_subject")
        data = event.get_json()
        parsed = urlsplit(data.get("url", ""))
        expected = urlsplit(os.environ["DATA_STORAGE_ACCOUNT_URL"])
        if (parsed.scheme, parsed.netloc, parsed.query, parsed.fragment) != (
                "https", expected.netloc, "", "") or parsed.path != f"/ready/{match[1]}":
            raise Rejected("invalid_event_blob_url")
        config = admission_config()
        store = services()[0]
        raw = store.read_bounded("ready", match[1], MAX_MANIFEST_BYTES)
        try:
            job = ingest(store, raw, f"ready/{match[1]}", config, limits()[0], enqueue=False)
            if job["status"] not in TERMINAL:
                await _start_orchestration(job["faxId"], client)
        except (Rejected, Conflict) as error:
            record_rejection(store, raw, f"ready/{match[1]}", config, str(error))
            raise
    except Exception as error:
        # Failed trigger attempts remain retryable and eventually reach the host's
        # poison queue; neither payloads nor SDK exception strings enter telemetry.
        log_stage_failure("blob_admission", error)
        raise RuntimeError("ready_admission_failed") from None


@app.route(route="faxes/{faxId}/admissions/{manifestSha256}", methods=["GET"])
def get_admission_attempt(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        fax = checked_id(req.route_params["faxId"])
        digest = req.route_params["manifestSha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return response({"error": "not_found"}, 404)
        record = services()[0].json("jobs", f"_admission-rejections/{fax}/{digest}.json")
        if (not actor.roles.intersection({"Fax.Admin", "Fax.Review"})
                and not ("Fax.Submit" in actor.roles and actor.key == record["configuredIntegration"])):
            return response({"error": "not_found"}, 404)
        return response({key: value for key, value in record.items() if key != "configuredIntegration"})
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_admission_evidence_temporarily_unavailable")
        return response({"error": "temporarily_unavailable"}, 503)


@app.route(route="faxes/{faxId}/review", methods=["GET", "POST"])
def review_fax(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        if "Fax.Review" not in actor.roles:
            raise PermissionError()
        fax = checked_id(req.route_params["faxId"])
        store, engine = services()
        if req.method == "GET":
            job = store.job(fax)
            evidence = store.json("human-review", f"{fax}/review.json")
            result = {"faxId": fax, "status": job["status"], "evidence": evidence,
                      "revision": job.get("reviewRevision", 0)}
            next_revision = job.get("reviewRevision", 0) + 1
            root = f"{fax}/revisions/{next_revision:06d}"
            if store.exists("human-review", root + "/decision.json"):
                result["reservation"] = {
                    "revision": next_revision,
                    "decision": store.json("human-review", root + "/decision.json"),
                    "manifestCommitted": store.exists("extracted", root + "/manifest.json"),
                    "abandonmentRecorded": store.exists("human-review", root + "/abandoned.json")}
            return response(result, headers={"ETag": review_etag(job)})
        if not processing_enabled():
            return response({"error": "processing_disabled"}, 503)
        etag = req.headers.get("if-match")
        if not etag:
            return response({"error": "if_match_required"}, 428)
        raw = req.get_body()
        if len(raw) > 512 * 1024:
            return response({"error": "review_size_limit"}, 413)
        result = engine.review(fax, actor, strict_json(raw), etag)
        return response(result)
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except Conflict:
        return response({"error": "review_version_conflict"}, 409)
    except Rejected as error:
        return response({"error": str(error)}, 422)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_review_temporarily_unavailable")
        return response({"error": "temporarily_unavailable_retry_same_review"}, 503)


@app.route(route="faxes/{faxId}/facts", methods=["GET"])
def get_facts(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        job = services()[0].job(checked_id(req.route_params["faxId"]))
        if actor.key != job["owner"] and not actor.roles.intersection({"Fax.Admin", "Fax.Review"}):
            return response({"error": "not_found"}, 404)
        return response(facts(job))
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_facts_temporarily_unavailable")
        return response({"error": "temporarily_unavailable"}, 503)


@app.route(route="faxes/{faxId}/review/original", methods=["GET"])
def review_original(req: func.HttpRequest) -> func.HttpResponse:
    """Serve the immutable original through the review role, never a public SAS."""
    try:
        actor = authenticate(req.headers, os.environ)
        if "Fax.Review" not in actor.roles:
            raise PermissionError()
        fax = checked_id(req.route_params["faxId"])
        store = services()[0]
        job = store.job(fax)
        if not store.exists("human-review", f"{fax}/review.json"):
            return response({"error": "not_found"}, 404)
        body = store.original(fax)
        suffix = "pdf" if job["mime"] == "application/pdf" else "tiff"
        return func.HttpResponse(body, mimetype=job["mime"], headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="original.{suffix}"',
            "Content-Security-Policy": "sandbox"})
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_review_original_temporarily_unavailable")
        return response({"error": "temporarily_unavailable"}, 503)


@app.route(route="faxes/{faxId}/review/reservations/{revision}/abandon", methods=["POST"])
def abandon_review(req: func.HttpRequest) -> func.HttpResponse:
    try:
        actor = authenticate(req.headers, os.environ)
        if "Fax.Review" not in actor.roles:
            raise PermissionError()
        if not processing_enabled():
            return response({"error": "processing_disabled"}, 503)
        if not req.headers.get("if-match"):
            return response({"error": "if_match_required"}, 428)
        fax = checked_id(req.route_params["faxId"])
        revision_text = req.route_params["revision"]
        if not re.fullmatch(r"[1-9][0-9]{0,5}", revision_text):
            raise Rejected("invalid_review_revision")
        raw = req.get_body()
        if len(raw) > 16384:
            return response({"error": "review_size_limit"}, 413)
        body = strict_json(raw)
        if not isinstance(body, dict) or set(body) != {"reason"}:
            raise Rejected("invalid_review_request")
        result = services()[1].abandon_review(fax, actor, int(revision_text), body["reason"],
                                              req.headers["if-match"])
        return response(result, headers={"ETag": result["reviewETag"]})
    except PermissionError:
        return response({"error": "forbidden"}, 403)
    except Conflict:
        return response({"error": "review_version_conflict"}, 409)
    except Rejected as error:
        return response({"error": str(error)}, 422)
    except (ValueError, KeyError, ResourceNotFoundError):
        return response({"error": "not_found"}, 404)
    except Exception:
        logging.error("fax_review_abandon_temporarily_unavailable")
        return response({"error": "temporarily_unavailable"}, 503)


async def _start_orchestration(fax, client):
    store, engine = services()
    terminal_runtime = False
    recovery_reason = "orchestration_terminal_recovery"
    with store.lock(fax) as lease:
        job = store.job(fax)
        if job["status"] in TERMINAL:
            return
        if not job.get("originalReady") or (job.get("admissionSha256") and not job.get("admissionCommitted")):
            raise RuntimeError("original_not_committed")
        if not job.get("orchestrationIntentAt"):
            # Persist intent before the Durable control-plane call. Initial admission
            # must not depend on an eventually consistent status lookup.
            job["orchestrationIntentAt"] = now()
            store.save_job(job, lease)
            await client.start_new("fax_pipeline", instance_id=fax, client_input={"faxId": fax})
            job["orchestrationStarted"] = True
            store.save_job(job, lease)
        else:
            status = await client.get_status(fax)
            if status is None:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(job["orchestrationIntentAt"])
                if age > timedelta(hours=1):
                    terminal_runtime = True
                    recovery_reason = "orchestration_start_unconfirmed"
            elif status.runtime_status in (
                    df.OrchestrationRuntimeStatus.Failed, df.OrchestrationRuntimeStatus.Terminated,
                    df.OrchestrationRuntimeStatus.Canceled, df.OrchestrationRuntimeStatus.Completed):
                terminal_runtime = True
    if terminal_runtime:
        engine.fail(fax, recovery_reason)


@app.queue_trigger(arg_name="message", queue_name="fax-jobs", connection="FaxQueueConnection")
@app.durable_client_input(client_name="client")
async def queue_starter(message: func.QueueMessage, client):
    # PROCESSING_ENABLED gates new admission. Already-admitted work must drain;
    # rejecting it here exhausts queue retries and can strand a valid job.
    try:
        envelope = json.loads(message.get_body())
        if set(envelope) != {"faxId"}:
            raise ValueError()
        fax = checked_id(envelope["faxId"])
        store, engine = services()
        terminal_runtime = False
        recovery_reason = "orchestration_terminal_recovery"
        with store.lock(fax) as lease:
            job = store.job(fax)
            if job["status"] in TERMINAL:
                return
            if not job.get("originalReady") or (job.get("admissionSha256") and not job.get("admissionCommitted")):
                raise RuntimeError("original_not_committed")
            status = await client.get_status(fax)
            if status is None:
                if job.get("orchestrationIntentAt"):
                    # start_new is an asynchronous queue handoff: get_status can be
                    # empty briefly after success. Never overwrite an instance in that
                    # ambiguity window. A long-unconfirmed handoff goes to review.
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(job["orchestrationIntentAt"])
                    if age > timedelta(hours=1):
                        terminal_runtime = True
                        recovery_reason = "orchestration_start_unconfirmed"
                else:
                    job["orchestrationIntentAt"] = now()
                    store.save_job(job, lease)
                    await client.start_new("fax_pipeline", instance_id=fax, client_input={"faxId": fax})
                    job["orchestrationStarted"] = True
                    store.save_job(job, lease)
            elif status.runtime_status in (
                    df.OrchestrationRuntimeStatus.Failed, df.OrchestrationRuntimeStatus.Terminated,
                    df.OrchestrationRuntimeStatus.Canceled, df.OrchestrationRuntimeStatus.Completed):
                terminal_runtime = True
        if terminal_runtime:
            engine.fail(fax, recovery_reason)
    except Exception:
        # Do not serialize SDK exception text (URLs/service error bodies) to host logs.
        raise RuntimeError("queue_start_failed") from None


@app.queue_trigger(arg_name="message", queue_name="fax-jobs-poison", connection="FaxQueueConnection")
@app.durable_client_input(client_name="client")
async def poison_handler(message: func.QueueMessage, client):
    try:
        envelope = json.loads(message.get_body())
        fax = checked_id(envelope["faxId"])
    except (ValueError, KeyError, TypeError):
        # Malformed payloads contain no trustworthy fax ID; retain in a restricted blob,
        # not logs. Hash naming bounds/reuses repeats without trusting message text.
        import hashlib
        raw = message.get_body()
        services()[0].immutable("jobs", "_poison/" + hashlib.sha256(raw).hexdigest(), raw,
                                "application/octet-stream")
        return
    try:
        store, engine = services()
        # Repair messages duplicate active jobs. Lease contention alone must not
        # let an exhausted duplicate cancel an otherwise healthy orchestration.
        with store.lock(fax) as lease:
            job = store.job(fax)
            if job["status"] in TERMINAL:
                return
            status = await client.get_status(fax)
            if status is not None and status.runtime_status not in (
                    df.OrchestrationRuntimeStatus.Failed, df.OrchestrationRuntimeStatus.Terminated,
                    df.OrchestrationRuntimeStatus.Canceled, df.OrchestrationRuntimeStatus.Completed):
                logging.warning("fax_poison_duplicate_active_instance")
                return
            if status is None and job.get("orchestrationIntentAt"):
                logging.warning("fax_poison_unconfirmed_handoff_deferred_to_outbox")
                return
            # Keep the decision and evidence commit under the same job lease so
            # a starter cannot begin processing between the check and the failure.
            engine._review(job, lease, "queue_poison", failed=True)
    except ResourceNotFoundError:
        logging.warning("fax_poison_job_not_found")
        return
    except Exception:
        raise RuntimeError("poison_record_failed") from None


@app.timer_trigger(arg_name="timer", schedule="0 */5 * * * *", use_monitor=True)
def repair_handoffs(timer: func.TimerRequest):
    if processing_enabled():
        try:
            services()[0].pending_page()
        except Exception:
            raise RuntimeError("outbox_repair_failed") from None


@app.orchestration_trigger(context_name="context")
def fax_pipeline(context: df.DurableOrchestrationContext):
    return (yield from orchestrate(context))


@app.activity_trigger(input_name="request")
def fax_activity(request: dict):
    try:
        fax = checked_id(request["faxId"])
        step = request["step"]
        engine = services()[1]
        if step in {"prepare", "remove_covers", "decide_type", "publish", "fail"}:
            return getattr(engine, step)(fax)
        if step in {"classify_start", "classify_poll", "type_start", "type_poll", "extract_start", "extract_poll"}:
            phase, action = step.split("_")
            return engine.ai_step(fax, phase, action)
        raise ValueError()
    except Exception:
        raise RuntimeError("fax_activity_failed") from None
