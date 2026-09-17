"""Demonstration-only HL7 v2.5.1; no clinical receiver compatibility is asserted."""

from hl7apy import VALIDATION_LEVEL
from hl7apy.parser import parse_message, parse_segment

from .contracts import PROFILES, select_profile, validated_values
from .policy import Rejected

MESSAGE_TYPES = {"exam": "ORU^R01^ORU_R01", "order": "ORM^O01^ORM_O01",
                 "rx": "RDE^O11^RDE_O11"}


def escape(value):
    text = str(value).lower() if type(value) is bool else str(value)
    for old, new in (("\\", "\\E\\"), ("|", "\\F\\"), ("^", "\\S\\"),
                     ("~", "\\R\\"), ("&", "\\T\\"), ("\r", "\\X0D\\"),
                     ("\n", "\\X0A\\"), ("\t", "\\X09\\")):
        text = text.replace(old, new)
    return text


def segment(name, fields):
    result = [""] * (max(fields) + 1)
    result[0] = name
    for number, value in fields.items():
        result[number] = value
    return "|".join(result)


def build(config, record):
    kind, source = record["documentType"], record["source"]
    values = validated_values(kind, record["values"])
    profile = select_profile(config, source, kind)
    if profile.get("status") == "NotApplicable":
        return None, profile
    v = {k: escape(value) for k, value in values.items()}
    timestamp = values["DocumentDate"].replace("-", "")
    control = record["faxId"].replace("-", "") + f"-r{record['revision']:06d}"
    lines = [
        "MSH|^~\\&|" + "|".join([
            profile["sendingApplication"], profile["sendingFacility"],
            profile["receivingApplication"], profile["receivingFacility"],
            timestamp, "", MESSAGE_TYPES[kind], control, "T", "2.5.1",
            "", "", "", "", "", "UNICODE UTF-8"]),
        segment("PID", {1: "1", 3: v["PatientId"], 5: v["PatientName"],
                        7: values["DateOfBirth"].replace("-", "")}),
        segment("PV1", {2: "U", 19: v["EncounterId"]}),
    ]
    if kind == "exam":
        lines += [
            segment("OBR", {1: "1", 4: "^" + v["ExamName"], 7: timestamp, 16: v["ProviderId"]}),
            segment("OBX", {1: "1", 2: "TX", 3: "^" + v["ExamName"], 5: v["ResultText"], 11: "U"}),
        ]
    else:
        lines.append(segment("ORC", {1: "NW", 9: timestamp, 12: v["ProviderId"]}))
        if kind == "order":
            lines.append(segment("OBR", {1: "1", 4: v["OrderCode"] + "^" + v["OrderDescription"],
                                         7: timestamp, 16: v["ProviderId"]}))
        else:
            # RXE.3 is NM in the base standard, so text dosing is held for a
            # configured NotApplicable disposition rather than silently coerced.
            dose = values["Dose"]
            if type(dose) is bool or not isinstance(dose, (str, int, float)):
                raise Rejected("invalid_hl7_dose")
            import re
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", str(dose)):
                raise Rejected("invalid_hl7_dose")
            lines += [
                segment("RXE", {1: "^" + v["Frequency"], 2: v["MedicationCode"] + "^" + v["MedicationName"],
                                3: v["Dose"], 5: profile["doseUnits"]}),
                segment("TQ1", {1: "1", 3: v["Frequency"]}),
                segment("RXR", {1: v["Route"]}),
            ]
    text = "\r".join(lines) + "\r"
    validate_message(text, kind, profile, control)
    return text.encode("utf-8"), {"status": "Validated", "profile": PROFILES[kind],
                                "version": "2.5.1", "messageType": MESSAGE_TYPES[kind],
                                "scope": "demonstration-only"}


def validate_message(text, kind, profile, control):
    """Strict syntax/type parser plus exact, bounded local demonstration profile."""
    try:
        parsed = parse_message(text, validation_level=VALIDATION_LEVEL.STRICT,
                               find_groups=True)
        if parsed.version != "2.5.1":
            raise ValueError()
        segments = [line.split("|") for line in text.rstrip("\r").split("\r")]
        expected = {"exam": ["MSH", "PID", "PV1", "OBR", "OBX"],
                    "order": ["MSH", "PID", "PV1", "ORC", "OBR"],
                    "rx": ["MSH", "PID", "PV1", "ORC", "RXE", "TQ1", "RXR"]}[kind]
        if [s[0] for s in segments] != expected:
            raise ValueError()
        msh = segments[0]
        if (msh[8] != MESSAGE_TYPES[kind] or msh[9] != control or msh[10:12] != ["T", "2.5.1"]
                or msh[2:6] != [profile[k] for k in ("sendingApplication", "sendingFacility",
                                                   "receivingApplication", "receivingFacility")]):
            raise ValueError()
        # hl7apy's ORM_O01 global choice-group validator treats mutually exclusive
        # OBR/RQD alternatives as simultaneously mandatory. Validate each segment
        # strictly and enforce our exact local message layout above instead.
        for line in text.rstrip("\r").split("\r"):
            parse_segment(line, version="2.5.1", validation_level=VALIDATION_LEVEL.STRICT).validate()
    except Exception:
        raise Rejected("hl7_profile_validation_failed") from None
