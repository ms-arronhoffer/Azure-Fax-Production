"""Deterministic, fictional TEST ONLY faxes; never import this into runtime."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

COMMON = ["PatientId", "PatientName", "DateOfBirth", "EncounterId", "DocumentDate", "ProviderId"]
SPECIFIC = {
    "exam": ["ExamName", "ResultText"],
    "order": ["OrderCode", "OrderDescription"],
    "rx": ["MedicationCode", "MedicationName", "Dose", "Route", "Frequency"],
}
SIZE = (1275, 1650)
VERSION = "synthetic-fax-v2"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def values(kind, split, number):
    key = f"{split.upper()}{number:04d}"
    if split == "train":
        prefixes = ("ALPHA", "B", "SYNTHETICINTAKE", "LONGREFERENCECODE", "SAMPLE", "ZETA")
        key = f"{prefixes[(number - 1) % len(prefixes)]}{number:04d}"
    result = dict(zip(COMMON, [
        f"TEST{key}", f"TESTPERSON {key}", f"1990-{number % 12 + 1:02d}-{number % 27 + 1:02d}",
        f"ENCOUNTER{key}", f"2026-08-{number % 27 + 1:02d}", f"PROVIDER{key}",
    ]))
    result.update({
        "exam": {"ExamName": f"FICTIONAL COLOR EXAM {number}", "ResultText": "TEST RESULT PURPLE CLOUD"},
        "order": {"OrderCode": f"TESTORDER{number}", "OrderDescription": "FICTIONAL RAINBOW SCAN DO NOT PERFORM"},
        "rx": {"MedicationCode": f"TESTMED{number}", "MedicationName": "IMAGINARY UNOBTAINIUM NOT A DRUG",
               "Dose": "0", "Route": "SIMULATION ONLY", "Frequency": "NEVER ADMINISTER"},
    }[kind])
    return result


def page(kind, fields=None, variant=0):
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=25)
    heading = ImageFont.load_default(size=38)
    draw.text((55, 35), "TEST ONLY - SYNTHETIC - NOT FOR CLINICAL USE", fill="black", font=font)
    title = {"cover": "FAX COVER SHEET - TRANSMITTAL ONLY",
             "exam": "EXAMINATION RESULTS", "order": "DIAGNOSTIC ORDER",
             "rx": "MEDICATION SIMULATION RECORD", "unknown": "OFFICE EQUIPMENT INVENTORY"}[kind]
    draw.text((55, 115), title, fill="black", font=heading)
    draw.line((55, 180, 1220, 180), fill="black", width=3)
    labels = []
    if kind == "cover":
        for i, text in enumerate(["To: TEST INTAKE LAB", "From: FICTIONAL FAX LAB",
                                  f"Transmission reference: TEST-{variant:04d}",
                                  "Cover is not a patient record. Discard cover before extraction."]):
            draw.text((55, 230 + i * 70), text, fill="black", font=font)
    elif kind == "unknown":
        draw.text((55, 250), "TEST CHAIRS: 42 / TEST DESKS: 17", fill="black", font=font)
    else:
        for i, (key, value) in enumerate(fields.items()):
            x, y = 55 + (variant % 3) * 5, 230 + i * 105
            draw.text((x, y), key, fill="black", font=font)
            value_y = y + 36
            draw.text((x, value_y), value, fill="black", font=font)
            box = draw.textbbox((x, value_y), value, font=font)
            left, top, right, bottom = box
            polygon = [left / SIZE[0], top / SIZE[1], right / SIZE[0], top / SIZE[1],
                       right / SIZE[0], bottom / SIZE[1], left / SIZE[0], bottom / SIZE[1]]
            labels.append({"label": key, "key": None, "value": [
                {"page": 1, "text": value, "boundingBoxes": [polygon]}]})
    draw.text((55, 1540), "FICTIONAL TEST ARTIFACT. NO REAL PERSON. NO TREATMENT INSTRUCTIONS.", fill="black", font=font)
    return image, labels


def save_pages(path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".pdf":
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=150,
                      creationDate=datetime(2026, 1, 1).timetuple(), modDate=datetime(2026, 1, 1).timetuple(),
                      title="TEST ONLY synthetic fax", author="Fictional test generator")
    else:
        pages[0].save(path, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_deflate",
                      dpi=(150, 150))


def generate(root, train_count=6):
    if not 5 <= train_count <= 20:
        raise ValueError("train-count must be 5..20 (bounded synthetic demonstration)")
    root = Path(root)
    if (root / "manifest.json").exists():
        raise ValueError("Output already contains a corpus; choose a new directory to preserve evidence")
    assets, cases = [], []

    def asset(relative, pages, split, kind, fields=None, labels=None):
        path = root / relative
        save_pages(path, pages)
        entry = {"path": relative, "split": split, "kind": kind, "sha256": digest(path.read_bytes()),
                 "pages": len(pages), "fields": fields or {}}
        assets.append(entry)
        if labels is not None:
            write_json(Path(str(path) + ".labels.json"), {"document": path.name, "labels": labels})
        return entry

    for split, count in [("train", train_count), ("development", 2)]:
        for kind in SPECIFIC:
            for number in range(1, count + 1):
                fields = values(kind, split, number)
                body, labels = page(kind, fields, number)
                if split == "train":
                    for prefix in [f"extract/{kind}", f"type/{kind}", "page/body"]:
                        relative = f"train/{prefix}/{kind}-{number:02d}.pdf"
                        asset(relative, [body], split, kind, fields, labels if prefix.startswith("extract") else None)
                    write_json(root / f"train/extract/{kind}/fields.json", {
                        "fields": [{"fieldKey": key, "fieldType": "string", "fieldFormat": "not-specified"}
                                   for key in COMMON + SPECIFIC[kind]]})
                else:
                    asset(f"development/{kind}-{number:02d}.pdf", [body], split, kind, fields)
        if split == "train":
            for number in range(1, count + 1):
                cover, _ = page("cover", variant=number)
                asset(f"train/page/cover/cover-{number:02d}.pdf", [cover], split, "cover")

    def case(name, kind, extension, covered=False, scenario="happy", routes="one", hl7="generated"):
        fields = values(kind if kind in SPECIFIC else "exam", "heldout", len(cases) + 101)
        if scenario == "missing-required":
            fields.pop("PatientId")
        body, _ = page(kind, fields)
        cover, _ = page("cover", variant=1000 + len(cases))
        pages = ([cover] if covered else []) + [body]
        if scenario == "cover-only":
            pages = [cover]
        if scenario == "mixed":
            pages.append(page("order", values("order", "heldout", 909))[0])
        item = asset(f"heldout/{name}.{extension}", pages, "heldout", kind, fields)
        cases.append({**item, "id": name, "scenario": scenario, "sourceProfile":
                      ("hl7-" + hl7 if hl7 != "generated" else routes),
                      "mime": "application/pdf" if extension == "pdf" else "image/tiff",
                      "bodyPages": 0 if scenario == "cover-only" else len(pages) - int(covered),
                      "coverPages": [1] if covered or scenario == "cover-only" else [],
                      "expected": {"state": "needs_review" if hl7 == "error" or scenario in {"missing-required", "unknown", "mixed", "cover-only"}
                                   else "complete", "documentType": kind, "fields": fields,
                                   "routeCount": {"none": 0, "one": 1, "multi": 2}[routes], "hl7": hl7}})
        if scenario == "missing-required":
            cases[-1]["reviewCorrection"] = {**fields, "PatientId": "TESTCORRECTED0001"}

    for kind in SPECIFIC:
        for extension in ["pdf", "tiff"]:
            for covered in [False, True]:
                case(f"{kind}-{extension}-{'cover' if covered else 'body'}", kind, extension, covered)
    for scenario, kind in [("missing-required", "exam"), ("unknown", "unknown"),
                           ("mixed", "exam"), ("cover-only", "cover")]:
        case(scenario, kind, "pdf", scenario == "cover-only", scenario)
    for routes in ["none", "multi"]:
        case(f"routes-{routes}", "exam", "pdf", routes=routes)
    case("hl7-not-applicable", "exam", "pdf", hl7="not_applicable")
    case("hl7-error", "exam", "pdf", hl7="error")
    for name, raw, mime in [("invalid-pdf", b"%PDF-1.7\nTEST ONLY INVALID", "application/pdf"),
                            ("invalid-tiff", b"II*\x00TEST ONLY INVALID", "image/tiff")]:
        relative = f"heldout/{name}.bin"
        (root / relative).write_bytes(raw)
        cases.append({"id": name, "path": relative, "sha256": digest(raw), "split": "heldout",
                      "scenario": "invalid", "mime": mime, "sourceProfile": "one",
                      "expected": {"state": "needs_review", "reason": "unreadable_document"}})
    manifest = {"schemaVersion": 1, "generatorVersion": VERSION, "testOnly": True,
                "trainingCountPerType": train_count, "assets": assets, "cases": cases,
                "separateProtocolTests": ["duplicate", "conflict", "admission-rejection", "human-corrected-release",
                                          "denied-review", "concurrent-review"],
                "limitations": "Synthetic engineering demonstration only; not clinical validation or confidence calibration."}
    write_json(root / "manifest.json", manifest)
    return manifest


def self_check(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    by_split = {}
    for entry in manifest["assets"] + manifest["cases"]:
        path = root / entry["path"]
        assert digest(path.read_bytes()) == entry["sha256"], path
        by_split.setdefault(entry["split"], set()).add(entry["sha256"])
        if "pages" in entry:
            count = len(PdfReader(path).pages) if path.suffix == ".pdf" else Image.open(path).n_frames
            assert count == entry["pages"], path
    assert not (by_split["train"] & by_split["heldout"]), "train/heldout content overlap"
    assert not (by_split["train"] & by_split["development"]), "train/development content overlap"
    assert not (by_split["development"] & by_split["heldout"]), "development/heldout overlap"
    for kind in SPECIFIC:
        samples = list((root / f"train/extract/{kind}").glob("*.labels.json"))
        assert len(samples) >= 5
        for path in samples:
            labels = json.loads(path.read_text())["labels"]
            assert {v["label"] for v in labels} == set(COMMON + SPECIFIC[kind])
            for label in labels:
                box = label["value"][0]["boundingBoxes"][0]
                assert len(box) == 8 and all(0 <= point <= 1 for point in box)
    return {"status": "passed", "assets": len(manifest["assets"]), "cases": len(manifest["cases"]),
            "liveInference": "not_run", "manifestSha256": digest((root / "manifest.json").read_bytes())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "self-check"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--train-count", type=int, default=6)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.root, args.train_count)
    print(json.dumps(self_check(args.root), indent=2))
