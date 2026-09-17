"""Storage boundary: immutable artifacts, leased job transitions and durable outbox."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import threading
import uuid

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.storage.queue import QueueClient, TextBase64EncodePolicy

from .policy import Conflict, Rejected

TERMINAL = {"Completed", "PendingHumanReview", "Failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


def credential():
    # Never fall back to developer identities in the deployed Function App.
    if os.environ.get("WEBSITE_INSTANCE_ID"):
        return ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


def endpoint(value, service):
    suffix = {"blob": "blob.core.windows.net", "queue": "queue.core.windows.net",
              "di": "cognitiveservices.azure.com"}[service]
    if not isinstance(value, str) or not re.fullmatch(
            r"https://[a-z0-9][a-z0-9-]{1,62}\." + re.escape(suffix) + r"/?", value):
        raise Rejected("invalid_service_endpoint")
    # ARM primaryEndpoints include a root slash; normalize it without accepting
    # arbitrary paths, query strings, credentials, or a different service host.
    return value.removesuffix("/")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checked_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_fax_id")
    return value


class Store:
    def __init__(self, identity=None):
        identity = identity or credential()
        self.blobs = BlobServiceClient(
            endpoint(os.environ["DATA_STORAGE_ACCOUNT_URL"], "blob"), credential=identity,
            retry_total=3, connection_timeout=10, read_timeout=30, logging_enable=False)
        self.queue = QueueClient(
            endpoint(os.environ["DATA_QUEUE_URL"], "queue"), "fax-jobs",
            credential=identity, message_encode_policy=TextBase64EncodePolicy(),
            retry_total=3, connection_timeout=10, read_timeout=30, logging_enable=False)

    def blob(self, container, name):
        return self.blobs.get_blob_client(container, name)

    def read(self, container, name):
        return self.blob(container, name).download_blob(logging_enable=False).readall()

    def read_bounded(self, container, name, maximum):
        data = self.blob(container, name).download_blob(
            offset=0, length=maximum + 1, logging_enable=False).readall()
        if len(data) > maximum:
            raise Rejected("invalid_upload_size")
        return data

    def original(self, fax):
        snapshot = f"{fax}/admitted-original"
        if self.exists("working", snapshot):
            return self.read("working", snapshot)
        return self.read("incoming", f"{fax}/original")

    def json(self, container, name):
        return json.loads(self.read(container, name))

    def commit_time(self, container, name):
        stamp = self.blob(container, name).get_blob_properties(logging_enable=False).last_modified
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise RuntimeError("manifest_commit_clock_unavailable")
        return stamp.isoformat()

    def exists(self, container, name):
        return self.blob(container, name).exists()

    def immutable(self, container, name, data, mime="application/json"):
        """Retries may replay writes, but may never change previously published bytes."""
        digest = hashlib.sha256(data).hexdigest()
        target = self.blob(container, name)
        try:
            target.upload_blob(data, overwrite=False, metadata={"sha256": digest},
                               content_settings=ContentSettings(content_type=mime),
                               logging_enable=False)
        except ResourceExistsError:
            if hashlib.sha256(self.read(container, name)).hexdigest() != digest:
                raise Conflict("artifact_content_conflict") from None
        return {"container": container, "name": name, "sha256": digest, "bytes": len(data)}

    def immutable_json(self, container, name, value):
        return self.immutable(container, name, encode(value))

    def create_job(self, job):
        try:
            self.blob("jobs", job["faxId"] + ".json").upload_blob(
                encode(job), overwrite=False, content_settings=ContentSettings(content_type="application/json"))
            return True
        except ResourceExistsError:
            return False

    def job(self, fax):
        return self.json("jobs", checked_id(fax) + ".json")

    @contextmanager
    def lock(self, fax):
        target = self.blob("jobs", checked_id(fax) + ".json")
        lease = target.acquire_lease(lease_duration=60)
        stop = threading.Event()
        errors = []

        def renew():
            while not stop.wait(20):
                try:
                    lease.renew()
                except Exception:
                    errors.append(True)
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            yield lease
            if errors:
                raise RuntimeError("job_lease_lost")
        finally:
            stop.set()
            heartbeat.join(timeout=35)
            try:
                lease.release()
            except Exception:
                if not errors:
                    raise

    def save_job(self, job, lease):
        job["updatedAt"] = now()
        self.blob("jobs", job["faxId"] + ".json").upload_blob(
            encode(job), overwrite=True, lease=lease,
            content_settings=ContentSettings(content_type="application/json"))

    def enqueue(self, fax):
        # Storage Queue's trigger decodes base64; no content or caller metadata is enqueued.
        self.queue.send_message(encode({"faxId": checked_id(fax)}).decode(), time_to_live=-1)

    def intake(self, fax, body, metadata, config, enqueue=True):
        job = {"faxId": fax, **metadata, "status": "Accepted", "createdAt": now(),
               "updatedAt": now(), "config": config, "originalReady": False}
        self.create_job(job)
        with self.lock(fax) as lease:
            saved = self.job(fax)
            if saved["fingerprint"] != metadata["fingerprint"]:
                raise Conflict("idempotency_payload_mismatch")
            if saved.get("admissionSha256") != metadata.get("admissionSha256"):
                raise Conflict("ready_manifest_conflict")
            if saved["status"] in TERMINAL:
                return saved
            self.immutable("incoming", f"{fax}/original", body, metadata["mime"])
            self.immutable("working", f"{fax}/admitted-original", body, metadata["mime"])
            if metadata.get("admissionSha256"):
                saved["admissionCommitted"] = True
            saved["originalReady"] = True
            self.save_job(saved, lease)
            # The record is the outbox. If enqueue fails, callers receive 503 and can
            # retry the identical request; the timer independently repairs the handoff.
            if enqueue:
                self.enqueue(fax)
            return saved

    def pending_page(self, maximum=200):
        cursor_blob = self.blob("jobs", "_reconcile.json")
        try:
            token = self.json("jobs", "_reconcile.json").get("token")
        except ResourceNotFoundError:
            token = None
        pages = self.blobs.get_container_client("jobs").list_blobs(
            results_per_page=maximum).by_page(continuation_token=token)
        try:
            items = list(next(pages))
        except StopIteration:
            items = []
        for item in items:
            if item.name.startswith("_"):
                continue
            job = self.json("jobs", item.name)
            # Repair the narrow crash window after immutable original upload but
            # before committing originalReady. Jobs without any original are not accepted.
            if (not job.get("originalReady") and not job.get("admissionSha256")
                    and self.exists("working", f"{job['faxId']}/admitted-original")):
                with self.lock(job["faxId"]) as lease:
                    job = self.job(job["faxId"])
                    job["originalReady"] = True
                    self.save_job(job, lease)
            if (job["status"] not in TERMINAL and job.get("originalReady")
                    and (not job.get("admissionSha256") or job.get("admissionCommitted"))):
                self.enqueue(job["faxId"])
        # Advancing only after a whole page succeeds preserves at-least-once repair.
        cursor_blob.upload_blob(encode({"token": pages.continuation_token}), overwrite=True)
