"""Typed domain exceptions (PRD §Appendix: preferred error-handling pattern).

Domain logic raises typed exceptions rather than returning sentinels, and a
centralized FastAPI exception handler converts them into the project's consistent
JSON error envelope ``{"error": ...}``. Nothing of the request is persisted when
the body is not even valid JSON (there is no schema to isolate it against).
"""


class MalformedJSONError(Exception):
    """The request body is not valid JSON and cannot be schema-validated or
    persisted (PRD Error States: 400 ``{"error": "malformed_json"}"``)."""