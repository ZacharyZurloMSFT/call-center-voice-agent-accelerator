"""Function calling module for Voice Live API."""

from .telehealth_functions import (
	build_patient_context,
	get_function_definitions,
	get_patient_contact_phone,
	get_patient_profile,
	handle_function_call,
)

__all__ = [
	"build_patient_context",
	"get_function_definitions",
	"get_patient_contact_phone",
	"get_patient_profile",
	"handle_function_call",
]
