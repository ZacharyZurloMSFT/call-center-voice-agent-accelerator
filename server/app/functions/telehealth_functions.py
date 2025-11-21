"""Function calling support for Voice Live API with mock tele-health data."""

import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MOCK_PATIENTS: Dict[str, Dict[str, Any]] = {
    "PATIENT001": {
        "name": "Avery Johnson",
        "date_of_birth": datetime(1985, 3, 12),
        "primary_physician": "Dr. Elena Ramirez",
        "contact_phone": "123-123-1234",
        "conditions": [
            "Type 2 Diabetes",
            "Hypertension",
        ],
        "medications": [
            {"name": "Metformin", "dosage": "500mg", "schedule": "Twice daily"},
            {"name": "Lisinopril", "dosage": "10mg", "schedule": "Once daily"},
        ],
        "recent_visits": [
            {"date": datetime(2025, 10, 2), "purpose": "Quarterly check-in"},
            {"date": datetime(2025, 7, 8), "purpose": "Medication review"},
        ],
    },
    "PATIENT002": {
        "name": "Jordan Lee",
        "date_of_birth": datetime(1992, 11, 4),
        "primary_physician": "Dr. Priya Sethi",
        "contact_phone": "555-987-6543",
        "conditions": ["Asthma"],
        "medications": [
            {"name": "Albuterol Inhaler", "dosage": "90 mcg", "schedule": "As needed"},
        ],
        "recent_visits": [
            {"date": datetime(2025, 9, 17), "purpose": "Pulmonary function test"},
            {"date": datetime(2025, 5, 29), "purpose": "Allergy evaluation"},
        ],
    },
}


SPECIALISTS = [
    "Dr. Kiera Morrison (Cardiology)",
    "Dr. Mateo Alvarez (Endocrinology)",
    "Dr. Yara Chen (Pulmonology)",
    "Dr. Samuel Blake (Dermatology)",
]

APPOINTMENT_WINDOWS = [
    "8:30 AM",
    "10:00 AM",
    "1:15 PM",
    "3:45 PM",
]

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _format_date(dt: datetime) -> str:
    return dt.strftime("%B %d, %Y")


def _format_recent_visits(visits: List[Dict[str, Any]]) -> str:
    if not visits:
        return "No recent visits recorded."
    formatted = [f"- {_format_date(v['date'])}: {v['purpose']}" for v in visits]
    return "\n".join(formatted)


def _format_medications(medications: List[Dict[str, Any]]) -> str:
    if not medications:
        return "No active medications on file."
    formatted = [
        f"- {m['name']} ({m['dosage']}, {m['schedule']})" for m in medications
    ]
    return "\n".join(formatted)


def fetch_patient_overview(patient_id: str) -> str:
    """Simulate a blocking call to a patient profile API."""
    profile = MOCK_PATIENTS.get(patient_id)
    if not profile:
        return "No matching patient record found."

    overview = [
        f"Patient Name: {profile['name']}",
        f"Date of Birth: {_format_date(profile['date_of_birth'])}",
        f"Primary Physician: {profile['primary_physician']}",
        f"Preferred Contact Phone: {profile.get('contact_phone', 'Not on file')}",
        "Active Conditions:",
        *(f"- {condition}" for condition in profile['conditions']),
        "",
        "Medication Plan:",
        _format_medications(profile['medications']),
        "",
        "Recent Visits:",
        _format_recent_visits(profile['recent_visits']),
    ]
    return "\n".join(str(line) for line in overview if line is not None)


# ---------------------------------------------------------------------------
# Voice Live tool definitions and handlers
# ---------------------------------------------------------------------------

SCHEDULE_APPOINTMENT_DEF: Dict[str, Any] = {
    "name": "schedule_appointment",
    "description": "Schedule a clinic or tele-health appointment for the patient",
    "parameters": {
        "type": "object",
        "properties": {
            "patient_id": {
                "type": "string",
                "description": "Unique patient identifier",
            },
            "appointment_type": {
                "type": "string",
                "description": "Reason for visit or appointment type",
            },
            "preferred_date": {
                "type": "string",
                "description": "Preferred appointment date (YYYY-MM-DD)",
            },
        },
        "required": ["patient_id", "appointment_type", "preferred_date"],
    },
}

GET_HEALTH_HISTORY_DEF: Dict[str, Any] = {
    "name": "get_patient_history",
    "description": "Retrieve a quick summary of the patient's recent health history",
    "parameters": {
        "type": "object",
        "properties": {
            "patient_id": {
                "type": "string",
                "description": "Unique patient identifier",
            }
        },
        "required": ["patient_id"],
    },
}

RENEW_PRESCRIPTION_DEF: Dict[str, Any] = {
    "name": "request_prescription_refill",
    "description": "Submit a refill request for an active prescription",
    "parameters": {
        "type": "object",
        "properties": {
            "patient_id": {
                "type": "string",
                "description": "Unique patient identifier",
            },
            "medication_name": {
                "type": "string",
                "description": "Name of the medication to refill",
            },
        },
        "required": ["patient_id", "medication_name"],
    },
}


async def schedule_appointment_handler(
    patient_id: str,
    appointment_type: str,
    preferred_date: str,
) -> str:
    profile = MOCK_PATIENTS.get(patient_id)
    patient_name = profile["name"] if profile else patient_id

    confirmation_number = f"APT-{random.randint(100000, 999999)}"
    available_slot = random.choice(APPOINTMENT_WINDOWS)
    specialist = random.choice(SPECIALISTS)

    logger.info(
        "Scheduling appointment for %s on %s (%s)",
        patient_id,
        preferred_date,
        appointment_type,
    )

    return (
        f"I scheduled a {appointment_type.lower()} appointment for {patient_name} on"
        f" {preferred_date} at {available_slot}. The visit will be with {specialist}."
        f" Confirmation number {confirmation_number}."
    )


async def get_patient_history_handler(patient_id: str) -> str:
    profile = MOCK_PATIENTS.get(patient_id)
    if not profile:
        logger.warning("Requested history for unknown patient %s", patient_id)
        return "I could not find a health history for that patient ID."

    conditions = ", ".join(profile["conditions"]) or "no chronic conditions"
    recent_visit = profile["recent_visits"][0] if profile["recent_visits"] else None
    visit_text = (
        f"Their most recent visit was on {_format_date(recent_visit['date'])}"
        f" for {recent_visit['purpose']}"
        if recent_visit
        else "There are no recent visits on file"
    )

    return (
        f"{profile['name']} is managed by {profile['primary_physician']}."
        f" They are currently being treated for {conditions}. {visit_text}."
    )


async def request_prescription_refill_handler(patient_id: str, medication_name: str) -> str:
    profile = MOCK_PATIENTS.get(patient_id)
    patient_name = profile["name"] if profile else patient_id
    logger.info(
        "Processing refill for patient %s medication %s", patient_id, medication_name
    )

    ready_date = (datetime.utcnow() + timedelta(days=1)).strftime("%B %d at %I:%M %p")
    return (
        f"I submitted a refill request for {medication_name} on behalf of {patient_name}."
        f" The prescription will be ready for pickup or delivery by {ready_date}."
    )


FUNCTION_HANDLERS = {
    "schedule_appointment": schedule_appointment_handler,
    "get_patient_history": get_patient_history_handler,
    "request_prescription_refill": request_prescription_refill_handler,
}

FUNCTION_DEFINITIONS = [
    SCHEDULE_APPOINTMENT_DEF,
    GET_HEALTH_HISTORY_DEF,
    RENEW_PRESCRIPTION_DEF,
]


def get_function_definitions() -> List[Dict[str, Any]]:
    return FUNCTION_DEFINITIONS


async def handle_function_call(function_name: str, arguments: Dict[str, Any]) -> str:
    handler = FUNCTION_HANDLERS.get(function_name)
    if not handler:
        logger.error("Unknown function: %s", function_name)
        return (
            "I'm sorry, I don't know how to help with that tele-health request right now."
        )

    try:
        return await handler(**arguments)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error calling tele-health function %s", function_name)
        return (
            "I encountered an issue while processing that tele-health request."
            " Please try again in a moment."
        )


def build_patient_context(patient_id: str) -> str:
    """Return a formatted patient overview string for use in the system prompt."""
    return fetch_patient_overview(patient_id)


def get_patient_contact_phone(patient_id: str) -> Optional[str]:
    """Return the preferred contact phone for the given patient if available."""
    profile = MOCK_PATIENTS.get(patient_id)
    if not profile:
        return None
    phone = profile.get("contact_phone")
    return str(phone) if phone else None


def get_patient_profile(patient_id: str) -> Optional[Dict[str, Any]]:
    """Return a structured patient profile suitable for background injection."""
    profile = MOCK_PATIENTS.get(patient_id)
    if not profile:
        return None

    visits = [
        {
            "date": visit["date"].isoformat(),
            "displayDate": _format_date(visit["date"]),
            "purpose": visit["purpose"],
        }
        for visit in profile.get("recent_visits", [])
    ]

    medications = [
        {
            "name": med["name"],
            "dosage": med["dosage"],
            "schedule": med["schedule"],
        }
        for med in profile.get("medications", [])
    ]

    return {
        "patientId": patient_id,
        "name": profile["name"],
        "dateOfBirth": profile["date_of_birth"].strftime("%Y-%m-%d"),
        "primaryPhysician": profile["primary_physician"],
        "contactPhone": profile.get("contact_phone"),
        "conditions": list(profile.get("conditions", [])),
        "medications": medications,
        "recentVisits": visits,
    }
