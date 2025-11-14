"""Function calling module for Voice Live API."""

from .order_functions import get_function_definitions, handle_function_call

__all__ = ["get_function_definitions", "handle_function_call"]
