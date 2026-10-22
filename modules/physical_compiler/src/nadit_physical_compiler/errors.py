"""Structured compiler outcomes shared by CLI and experiment workers."""


class SearchExhausted(ValueError):
    """A bounded placement search ended without a completed goal."""
