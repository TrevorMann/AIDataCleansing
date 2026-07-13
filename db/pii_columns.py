"""Column-name heuristic for sensitive/PII columns — used to skip/redact raw
value sampling before it's sent to an LLM (annotation, seed research)."""

import re

# Insert an underscore at camelCase humps (customerEmail -> customer_Email)
# before lowercasing, so snake_case and camelCase column names both reduce
# to the same underscore-delimited form for boundary matching below.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_PII_PATTERN = re.compile(
    r"(^|_)(ssn|social_security|emails?|e_mail|phones?|telephone|"
    r"dob|date_of_birth|birth_date|password|passwd|"
    r"credit_card|card_number|cvv|"
    r"drivers_license|passport|tax_id|sin)(_|$)"
)

REDACTED = "<redacted>"


def is_pii_column(column_name: str) -> bool:
    normalized = _CAMEL_BOUNDARY.sub("_", column_name).lower()
    return bool(_PII_PATTERN.search(normalized))
