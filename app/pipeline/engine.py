"""Activity implementation. Every result sent to Durable is a safe state only."""

import hashlib
import json
import logging

from .documents import normalize, select_pages
from .policy import Conflict, Rejected, gate, matching_routes, page_plan, validate_config
from .contracts import canonical, classify_body, review_request, result_type_matches
from .hl7 import build as build_hl7
from .reports import facts, review_etag
from .storage import TERMINAL, encode, now


class Engine:
    def __init__(self, store, intelligence, max_pages=100):
        self.store, self.ai, self.max_pages = store, intelligence, max_pages

    def work_json(self, fax, filename):
        return self.store.json("working", f"{fax}/{filename}")

    def plan(self, job):
        filename = "final-plan.json" if job["config"].get("schemaVersion") == 2 else "page-plan.json"
        return self.work_json(job["faxId"], filename)

    def _stamp(self, job, phase, event):
        name = f"{job['faxId']}/timing-{phase}-{event}.json"
        if not self.store.exists("working", name):
            self.store.immutable_json("working", name, {"at": now()})
        stamp = self.store.json("working", name)["at"]
        job.setdefault("phaseTimings", {}).setdefault(phase, {})[event] = stamp

    def _terminal(self, job, lease):
        if job["status"] in TERMINAL:
            return True
        fax = job["faxId"]
        if self.store.exists("extracted", f"{fax}/manifest.json"):
            self._complete(job, lease)
            return True
        if self.store.exists("human-review", f"{fax}/review.json"):
            self._review(job, lease, "review_commit_recovery")
            return True
        return False

    def prepare(self, fax):
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if self._terminal(job, lease):
                return {"state": "terminal"}
            if self.store.exists("working", f"{fax}/prepared.json"):
                job["pageCount"] = self.work_json(fax, "prepared.json")["pageCount"]
                if job["status"] == "Accepted":
                    job["status"] = "Processing"
                if "prepare" in job.get("phaseTimings", {}):
                    self._stamp(job, "prepare", "completedAt")
                self.store.save_job(job, lease)
                return {"state": "ready", "typeClassifier": job["config"].get("schemaVersion") == 2}
            try:
                self._stamp(job, "prepare", "startedAt")
                self.store.save_job(job, lease)
                config = validate_config(job["config"])
                self.ai.preflight(config)
                original = self.store.original(fax)
                pdf, count = normalize(original, job["mime"], self.max_pages)
                self.store.immutable("working", f"{fax}/normalized.pdf", pdf, "application/pdf")
                self.store.immutable_json("working", f"{fax}/prepared.json", {"pageCount": count})
                job["status"] = "Processing"
                job["pageCount"] = count
                self._stamp(job, "prepare", "completedAt")
                self.store.save_job(job, lease)
                return {"state": "ready", "typeClassifier": config.get("schemaVersion") == 2}
            except Rejected as error:
                self._review(job, lease, str(error))
                return {"state": "terminal"}

    def ai_step(self, fax, phase, action):
        """LRO URLs/results stay in working storage, never orchestration inputs."""
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if self._terminal(job, lease):
                return {"state": "terminal"}
            raw_name = f"{fax}/{phase}-result.json"
            if self.store.exists("working", raw_name):
                if phase in job.get("phaseTimings", {}):
                    self._stamp(job, phase, "completedAt")
                    self.store.save_job(job, lease)
                return {"state": "succeeded"}
            if phase == "classify":
                kind, model = "documentClassifiers", job["config"]["classifier"]["id"]
                input_name = "normalized.pdf"
            elif phase == "type":
                kind, model = "documentClassifiers", job["config"]["faxTypeClassifier"]["id"]
                input_name = "body.pdf"
            elif phase == "extract":
                plan = self.plan(job)
                kind = "documentModels"
                model = job["config"]["documentTypes"][plan["documentType"]]["modelId"]
                input_name = "body.pdf"
            else:
                raise Rejected("invalid_analysis_phase")
            operation_name = f"{fax}/{phase}-operation.json"
            try:
                self._stamp(job, phase, "startedAt")
                self.store.save_job(job, lease)
                if not self.store.exists("working", operation_name):
                    body = self.store.read("working", f"{fax}/{input_name}")
                    operation = (self.ai.start(kind, model, body, split="auto") if phase == "type"
                                 else self.ai.start(kind, model, body))
                    self.store.immutable_json("working", operation_name, {"url": operation, "modelId": model})
                model_role = {"classify": "pageClassifier", "type": "faxTypeClassifier", "extract": "extractor"}[phase]
                job.setdefault("modelIds", {})[model_role] = model
                self.store.save_job(job, lease)
                if action == "start":
                    return {"state": "running"}
                operation = self.store.json("working", operation_name)["url"]
                result = self.ai.poll(operation, kind, model)
                if result["status"] == "failed":
                    self._stamp(job, phase, "completedAt")
                    self._review(job, lease, "analysis_failed")
                    return {"state": "terminal"}
                if result["status"] == "succeeded":
                    self.store.immutable_json("working", raw_name, result["analyzeResult"])
                    self._stamp(job, phase, "completedAt")
                    self.store.save_job(job, lease)
                    return {"state": "succeeded"}
                return {"state": "running"}
            except Rejected as error:
                self._review(job, lease, str(error))
                return {"state": "terminal"}

    def remove_covers(self, fax):
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if self._terminal(job, lease):
                return {"state": "terminal"}
            try:
                self._stamp(job, "removeCovers", "startedAt")
                self.store.save_job(job, lease)
                prepared = self.work_json(fax, "prepared.json")
                plan = page_plan(self.work_json(fax, "classify-result.json"),
                                 prepared["pageCount"], job["config"]["classifier"])
                # Persist the original->body mapping before extraction for reviewer provenance.
                self.store.immutable_json("working", f"{fax}/page-plan.json", plan)
                body = select_pages(self.store.read("working", f"{fax}/normalized.pdf"), plan["bodyPages"])
                self.store.immutable("working", f"{fax}/body.pdf", body, "application/pdf")
                job["bodyPageCount"], job["coverPageCount"] = len(plan["bodyPages"]), len(plan["coverPages"])
                self._stamp(job, "removeCovers", "completedAt")
                self.store.save_job(job, lease)
                return {"state": "ready"}
            except Rejected as error:
                self._review(job, lease, str(error))
                return {"state": "terminal"}

    def decide_type(self, fax):
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if self._terminal(job, lease):
                return {"state": "terminal"}
            try:
                plan = self.work_json(fax, "page-plan.json")
                decision = classify_body(self.work_json(fax, "type-result.json"),
                                         len(plan["bodyPages"]), job["config"]["faxTypeClassifier"])
                self.store.immutable_json("working", f"{fax}/type-decision.json", decision)
                self.store.immutable_json("working", f"{fax}/final-plan.json",
                                          {**plan, "documentType": decision["documentType"]})
                job["documentType"] = decision["documentType"]
                job["faxTypeConfidence"] = decision["confidence"]
                self.store.save_job(job, lease)
                return {"state": "ready"}
            except Rejected as error:
                self._review(job, lease, str(error))
                return {"state": "terminal"}

    def publish(self, fax):
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if self._terminal(job, lease):
                return {"state": "terminal"}
            self._stamp(job, "publish", "startedAt")
            self.store.save_job(job, lease)
            plan = self.plan(job)
            result = self.work_json(fax, "extract-result.json")
            spec = job["config"]["documentTypes"][plan["documentType"]]
            docs = result.get("documents", [])
            if len(docs) != 1 or not result_type_matches(docs[0].get("docType"), spec["resultDocType"], spec["modelId"]):
                self._review(job, lease, "ambiguous_extraction")
                return {"state": "terminal"}
            decision = gate(docs[0].get("fields", {}), spec["expectedFields"])
            self.store.immutable_json("working", f"{fax}/gate.json", decision)
            job["fieldMinimumConfidence"] = decision["minimumConfidence"]
            if not decision["passed"]:
                self._review(job, lease, "field_confidence_gate")
                return {"state": "terminal"}
            try:
                record = canonical(job, plan["documentType"],
                                   {key: items[0] for key, items in decision["values"].items()},
                                   plan, "Automatic")
            except Rejected as error:
                self._review(job, lease, str(error))
                return {"state": "terminal"}
            try:
                hl7, disposition = build_hl7(job["config"], record)
            except Rejected as error:
                job["hl7Disposition"] = {"status": "Rejected"}
                self._review(job, lease, str(error))
                return {"state": "terminal"}
            routes = matching_routes(job["config"], job["source"], plan["documentType"],
                                     result.get("content", ""), decision["values"])
            payload = {
                "schemaVersion": 1, "faxId": fax, "source": job["source"],
                "documentType": plan["documentType"], "modelId": spec["modelId"],
                "apiVersion": "2024-11-30", "originalPageMap": plan,
                "content": result.get("content", ""), "fields": docs[0]["fields"],
                "tables": result.get("tables", []), "gate": decision,
                "configSha256": hashlib.sha256(encode(job["config"])).hexdigest(),
                "canonical": record, "hl7Disposition": disposition,
            }
            data = encode(payload)
            job["status"] = "Publishing"
            job["publicationStartedAt"] = job.get("publicationStartedAt", now())
            job["phaseTimings"]["publish"]["decisionAt"] = job["publicationStartedAt"]
            job["hl7Disposition"] = disposition
            job["matchedRouteCount"] = len(routes)
            job["publication"] = {"plannedRoutes": routes, "manifestReady": False}
            self.store.save_job(job, lease)
            # Fan-out is not a multi-blob transaction. A completed manifest is the
            # commit marker; consumers MUST NOT watch individual artifact creation.
            artifacts = [self.store.immutable("extracted", f"{fax}/content.json", data)]
            artifacts.append(self.store.immutable_json("extracted", f"{fax}/canonical.json", record))
            if hl7 is not None:
                artifacts.append(self.store.immutable("extracted", f"{fax}/content.hl7", hl7, "application/hl7-v2"))
            for route in routes:
                lease.renew()
                artifacts.append(self.store.immutable("routed", f"{route}/{fax}/content.json", data))
                if hl7 is not None:
                    artifacts.append(self.store.immutable("routed", f"{route}/{fax}/content.hl7", hl7,
                                                          "application/hl7-v2"))
            self._stamp(job, "publish", "preparedAt")
            job["completionPreparedAt"] = job["phaseTimings"]["publish"]["preparedAt"]
            job["completionPreparationRevision"] = 0
            self.store.save_job(job, lease)
            completed = {**job, "status": "Completed", "completionMode": "Automatic",
                         "manifest": {"container": "extracted", "name": f"{fax}/manifest.json"},
                         "publication": {"manifestReady": True}}
            artifacts.append(self._report(completed, f"{fax}/report.json"))
            lease.renew()
            manifest = {"schemaVersion": 1, "faxId": fax, "status": "Completed",
                        "gatePassed": True, "routes": routes, "artifacts": artifacts,
                        "configSha256": payload["configSha256"], "completionMode": "Automatic",
                        "hl7Disposition": disposition, "provenance": record["provenance"]}
            self.store.immutable_json("extracted", f"{fax}/manifest.json", manifest)
            self._complete(job, lease)
            return {"state": "completed"}

    def _complete(self, job, lease):
        job["status"] = "Completed"
        job["completionMode"] = "Automatic"
        job["manifest"] = {"container": "extracted", "name": f"{job['faxId']}/manifest.json"}
        job["completedAt"] = self.store.commit_time("extracted", job["manifest"]["name"])
        job["completedAtSource"] = "manifestLastModified"
        job["publication"] = {"manifestReady": True}
        self.store.save_job(job, lease)

    def _report(self, job, name):
        snapshot = {key: value for key, value in job.items() if key not in {"completedAt", "completedAtSource"}}
        report = facts(snapshot)
        if job["status"] == "Completed":
            report["snapshotPhase"] = "completion-prepared"
            report["updatedAt"] = job["completionPreparedAt"]
        else:
            report["updatedAt"] = job.get("reviewEnteredAt", job["createdAt"])
        artifact = self.store.immutable_json(
            "extracted" if job["status"] == "Completed" else "human-review", name, report)
        # Only the bounded, PHI-free reporting contract is emitted to telemetry.
        logging.info("FAX_METRIC %s", json.dumps(report, sort_keys=True, separators=(",", ":")))
        return artifact

    def _review(self, job, lease, reason, failed=False):
        fax = job["faxId"]
        if job["status"] in TERMINAL:
            return
        # If the manifest was committed and only the status write failed, recovery
        # must finish the commit instead of declaring a successfully published fax failed.
        if self.store.exists("extracted", f"{fax}/manifest.json"):
            self._complete(job, lease)
            return
        if self.store.exists("human-review", f"{fax}/review.json"):
            # Recover a crash after review evidence commit but before status commit.
            # The first decision wins even if recovery arrived via a different failure path.
            committed = self.store.json("human-review", f"{fax}/review.json")
            job["status"] = committed["jobStatus"]
            job["reason"] = committed["reason"]
            job["reviewEnteredAt"] = committed.get("reviewEnteredAt", job["createdAt"])
            if "minimumConfidence" in committed["gate"]:
                job["fieldMinimumConfidence"] = committed["gate"]["minimumConfidence"]
            job["review"] = {"container": "human-review", "name": f"{fax}/review.json"}
            self.store.save_job(job, lease)
            self._report(job, f"{fax}/report.json")
            return
        self.store.immutable("human-review", f"{fax}/original",
                             self.store.original(fax), job["mime"])
        plan_name = "final-plan.json" if self.store.exists("working", f"{fax}/final-plan.json") else "page-plan.json"
        plan = self.work_json(fax, plan_name) if self.store.exists(
            "working", f"{fax}/{plan_name}") else {"bodyPages": [], "coverPages": [],
                                                "mappingUnavailable": True}
        decision = self.work_json(fax, "gate.json") if self.store.exists(
            "working", f"{fax}/gate.json") else {"passed": False, "scores": {}, "values": {}}
        fields = {}
        if self.store.exists("working", f"{fax}/extract-result.json"):
            fields = [d.get("fields", {}) for d in self.work_json(fax, "extract-result.json").get("documents", [])]
        if "reviewIntent" not in job:
            job["reviewIntent"] = {"reason": reason, "jobStatus": "Failed" if failed else "PendingHumanReview",
                                   "reviewEnteredAt": now()}
            self.store.save_job(job, lease)
        intent = job["reviewIntent"]
        review = {"schemaVersion": 1, "faxId": fax, "status": "PendingHumanReview",
                  "reviewEnteredAt": intent["reviewEnteredAt"],
                  "jobStatus": intent["jobStatus"],
                  "reason": intent["reason"], "originalPageMap": plan, "gate": decision, "fields": fields,
                  "original": {"container": "human-review", "name": f"{fax}/original"},
                  "partialPublication": job["status"] == "Publishing",
                  "classifications": {phase: self.work_json(fax, f"{phase}-result.json")
                                      for phase in ("classify", "type")
                                      if self.store.exists("working", f"{fax}/{phase}-result.json")},
                  "releaseImplemented": True}
        job["status"] = intent["jobStatus"]
        job["reason"] = intent["reason"]
        job["reviewEnteredAt"] = review["reviewEnteredAt"]
        job["review"] = {"container": "human-review", "name": f"{fax}/review.json"}
        review["report"] = self._report(job, f"{fax}/report.json")
        self.store.immutable_json("human-review", f"{fax}/review.json", review)
        self.store.save_job(job, lease)

    def review(self, fax, actor, body, etag):
        if "Fax.Review" not in actor.roles:
            raise PermissionError()
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if etag != review_etag(job):
                raise Conflict("review_version_conflict")
            if job["status"] not in {"PendingHumanReview", "Failed"}:
                raise Conflict("review_not_pending")
            original = self.store.original(fax)
            normalized, count = normalize(original, job["mime"], self.max_pages)
            plan, values = review_request(body, count)
            revision = job.get("reviewRevision", 0) + 1
            root = f"{fax}/revisions/{revision:06d}"
            record = canonical(job, body["documentType"], values, plan, "Reviewed", revision)
            hl7, disposition = build_hl7(job["config"], record)
            original_review = self.store.json("human-review", f"{fax}/review.json")
            request_digest = hashlib.sha256(encode({"body": body, "actor": actor.key})).hexdigest()
            intent_name = f"{root}/decision.json"
            if self.store.exists("human-review", root + "/abandoned.json"):
                raise Conflict("review_reservation_abandoned")
            if self.store.exists("human-review", intent_name):
                evidence = self.store.json("human-review", intent_name)
                if evidence["requestSha256"] != request_digest:
                    raise Conflict("review_revision_reserved")
            else:
                evidence = {"schemaVersion": 2, "faxId": fax, "revision": revision,
                            "requestSha256": request_digest, "reviewer": {"tenant": actor.tenant, "oid": actor.oid},
                            "reviewedAt": now(), "reason": body["reason"], "values": values,
                            "originalPageMap": plan, "originalEvidence": original_review,
                            "confidencePolicy": "original scores preserved; human authorization is not a score"}
                self.store.immutable_json("human-review", intent_name, evidence)
            artifacts = [self.store.immutable_json("extracted", root + "/canonical.json", record)]
            artifacts.append(self.store.immutable("extracted", root + "/body.pdf",
                                                   select_pages(normalized, plan["bodyPages"]), "application/pdf"))
            if hl7 is not None:
                artifacts.append(self.store.immutable("extracted", root + "/content.hl7", hl7, "application/hl7-v2"))
            payload = {"schemaVersion": 2, "canonical": record, "reviewEvidence":
                       {"container": "human-review", "name": intent_name},
                       "originalGate": original_review["gate"], "hl7Disposition": disposition}
            artifacts.append(self.store.immutable_json("extracted", root + "/content.json", payload))
            # Content-based routing must use corrected values, not original unreliable OCR.
            routes = matching_routes(job["config"], job["source"], body["documentType"],
                                     "\n".join(str(v) for v in values.values()),
                                     {key: [value] for key, value in values.items()})
            for route in routes:
                lease.renew()
                artifacts.append(self.store.immutable_json("routed", f"{route}/{root}/content.json", payload))
                if hl7 is not None:
                    artifacts.append(self.store.immutable("routed", f"{route}/{root}/content.hl7", hl7,
                                                          "application/hl7-v2"))
            preparation = root + "/completion-prepared.json"
            if not self.store.exists("working", preparation):
                self.store.immutable_json("working", preparation, {"at": now()})
            job["completionPreparedAt"] = self.store.json("working", preparation)["at"]
            job["completionPreparationRevision"] = revision
            self.store.save_job(job, lease)
            completed = {**job, "status": "Completed", "completionMode": "Reviewed", "reviewRevision": revision,
                         "documentType": body["documentType"],
                         "bodyPageCount": len(plan["bodyPages"]), "coverPageCount": len(plan["coverPages"]),
                         "manifest": {"container": "extracted", "name": root + "/manifest.json"},
                         "publication": {"manifestReady": True},
                         "hl7Disposition": disposition, "matchedRouteCount": len(routes)}
            completed.setdefault("phaseTimings", {})["review"] = {
                "startedAt": job.get("reviewEnteredAt", original_review.get("reviewEnteredAt", job["createdAt"])),
                "decisionAt": evidence["reviewedAt"], "preparedAt": job["completionPreparedAt"]}
            artifacts.append(self._report(completed, root + "/report.json"))
            manifest = {"schemaVersion": 2, "faxId": fax, "revision": revision, "status": "Completed",
                        "completionMode": "Reviewed", "gatePassed": original_review["gate"]["passed"],
                        "reviewAuthorized": True, "reviewEvidence": payload["reviewEvidence"],
                        "hl7Disposition": disposition, "routes": routes, "artifacts": artifacts,
                        "configSha256": hashlib.sha256(encode(job["config"])).hexdigest(),
                        "provenance": record["provenance"]}
            self.store.immutable_json("extracted", root + "/manifest.json", manifest)
            completed["completedAt"] = self.store.commit_time("extracted", root + "/manifest.json")
            completed["completedAtSource"] = "manifestLastModified"
            job.update(completed)
            self.store.save_job(job, lease)
            return {"faxId": fax, "status": "Completed", "completionMode": "Reviewed",
                    "revision": revision, "manifest": job["manifest"]}

    def abandon_review(self, fax, actor, revision, reason, etag):
        """Supersede an uncommitted intent without deleting evidence or reusing IDs."""
        if "Fax.Review" not in actor.roles:
            raise PermissionError()
        if type(revision) is not int or not 1 <= revision <= 999999:
            raise Rejected("invalid_review_revision")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            raise Rejected("invalid_review_reason")
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            if (etag != review_etag(job) or revision != job.get("reviewRevision", 0) + 1
                    or job["status"] not in {"PendingHumanReview", "Failed"}):
                raise Conflict("review_version_conflict")
            root = f"{fax}/revisions/{revision:06d}"
            if self.store.exists("extracted", root + "/manifest.json"):
                raise Conflict("review_already_committed")
            if not self.store.exists("human-review", root + "/decision.json"):
                raise Conflict("review_reservation_not_found")
            name = root + "/abandoned.json"
            if not self.store.exists("human-review", name):
                self.store.immutable_json("human-review", name, {
                    "schemaVersion": 1, "faxId": fax, "revision": revision, "status": "Abandoned",
                    "reviewer": {"tenant": actor.tenant, "oid": actor.oid},
                    "abandonedAt": now(), "reason": reason,
                    "decision": {"container": "human-review", "name": root + "/decision.json"}})
            # If the evidence committed before a failed status write, any authorized
            # retry finalizes the already-recorded abandonment, not a new decision.
            job["reviewRevision"] = revision
            if job.get("completionPreparationRevision") == revision:
                job.pop("completionPreparedAt", None)
                job.pop("completionPreparationRevision", None)
            self.store.save_job(job, lease)
            return {"faxId": fax, "status": job["status"], "abandonedRevision": revision,
                    "reviewETag": review_etag(job)}

    def fail(self, fax, reason="pipeline_failed"):
        with self.store.lock(fax) as lease:
            job = self.store.job(fax)
            self._review(job, lease, reason, failed=True)
        return {"state": "terminal"}
