"""Function calling support for Voice Live API: patient identity verification.

This module exposes a single tool, ``verify_patient_identity``, which checks a
caller-supplied name, date of birth, and phone number against a mock patient
directory. Matching is strict but tolerant of formatting differences:

* name  -> case-insensitive, whitespace-collapsed exact match
* dob   -> normalized to ``YYYY-MM-DD`` before compare
* phone -> stripped to digits only before compare
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock patient directory
# ---------------------------------------------------------------------------

MOCK_PATIENTS: List[Dict[str, str]] = [
    {"name": "Avery Johnson", "date_of_birth": "1985-03-12", "phone_number": "123-123-1234"},
    {"name": "Jordan Lee",    "date_of_birth": "1992-11-04", "phone_number": "555-987-6543"},
    {"name": "Sam Rivera",    "date_of_birth": "1978-06-30", "phone_number": "555-222-8899"},
]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")

_DOB_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m/%d/%y",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)


def _normalize_name(value: str) -> str:
    return _WS_RE.sub(" ", (value or "").strip()).lower()


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _normalize_dob(value: str) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Voice Live tool definition + handler
# ---------------------------------------------------------------------------

VERIFY_PATIENT_DEF: Dict[str, Any] = {
    "name": "verify_patient_identity",
    "description": (
        "Verify a caller's identity by matching their full name, date of birth, "
        "and phone number against the patient directory. Only call this tool "
        "after all three values have been collected from the caller."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The patient's full name as spoken by the caller.",
            },
            "date_of_birth": {
                "type": "string",
                "description": (
                    "The patient's date of birth. Any common format is accepted "
                    "(e.g. 1985-03-12, 03/12/1985, March 12 1985)."
                ),
            },
            "phone_number": {
                "type": "string",
                "description": (
                    "The patient's phone number. Digits only or common formats "
                    "with dashes, spaces, or parentheses are accepted."
                ),
            },
        },
        "required": ["name", "date_of_birth", "phone_number"],
    },
}


async def verify_patient_identity_handler(
    name: str,
    date_of_birth: str,
    phone_number: str,
) -> str:
    normalized_name = _normalize_name(name)
    normalized_dob = _normalize_dob(date_of_birth)
    normalized_phone = _normalize_phone(phone_number)

    if not normalized_name or not normalized_dob or not normalized_phone:
        logger.info(
            "verify_patient_identity: incomplete inputs (name=%s dob=%s phone=%s)",
            bool(normalized_name),
            bool(normalized_dob),
            bool(normalized_phone),
        )
        return (
            "DECLINED: I need a full name, a valid date of birth, and a phone number "
            "to verify the caller."
        )

    for record in MOCK_PATIENTS:
        if (
            _normalize_name(record["name"]) == normalized_name
            and _normalize_dob(record["date_of_birth"]) == normalized_dob
            and _normalize_phone(record["phone_number"]) == normalized_phone
        ):
            logger.info("verify_patient_identity: CONFIRMED match for %s", record["name"])
            return (
                f"CONFIRMED: identity verified. Name, date of birth, and phone number "
                f"all match the record for {record['name']}."
            )

    logger.info(
        "verify_patient_identity: DECLINED (no full match for name=%r dob=%r phone=%r)",
        normalized_name,
        normalized_dob,
        normalized_phone,
    )
    return (
        "DECLINED: I could not verify the caller. The name, date of birth, and phone "
        "number do not all match a single patient record."
    )


FUNCTION_HANDLERS = {
    "verify_patient_identity": verify_patient_identity_handler,
}

FUNCTION_DEFINITIONS = [VERIFY_PATIENT_DEF]


def get_function_definitions() -> List[Dict[str, Any]]:
    return FUNCTION_DEFINITIONS


async def handle_function_call(function_name: str, arguments: Dict[str, Any]) -> str:
    handler = FUNCTION_HANDLERS.get(function_name)
    if not handler:
        logger.error("Unknown function: %s", function_name)
        return "I'm sorry, I can only help with verifying a caller's identity right now."

    try:
        return await handler(**arguments)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Error calling function %s", function_name)
        return (
            "I ran into a problem while trying to verify that information. "
            "Please try again in a moment."
        )
