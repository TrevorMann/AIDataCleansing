import re

# Mapping of abbreviations to full forms
ABBREVIATION_MAP = {
    r'\bSt\.?$': 'Street',
    r'\bAve\.?$': 'Avenue',
    r'\bBlvd\.?$': 'Boulevard',
    r'\bRd\.?$': 'Road',
    r'\bDr\.?$': 'Drive',
    r'\bLn\.?$': 'Lane',
    r'\bPl\.?$': 'Place',
    r'\bCir\.?$': 'Circle',
    r'\bCt\.?$': 'Court',
    r'\bTerr\.?$': 'Terrace',
}

def standardize_address(address: str) -> str:
    """
    Normalize street address for cache matching.

    Steps:
    1. Strip house number (leading digits)
    2. Strip suffix after comma (apt, suite, etc)
    3. Strip trailing/leading whitespace
    4. Keep directionals (N, S, E, W, NE, NW, SE, SW)
    5. Expand abbreviations (St → Street, Ave → Avenue)
    6. Normalize casing
    7. Collapse multiple spaces
    8. Strip punctuation
    """
    if not address:
        return ""

    # 1. Strip house number (leading digits + space)
    addr = re.sub(r'^\d+\s+', '', address.strip())

    # 2. Strip suffix after comma (apt, suite, floor, etc)
    addr = re.split(r',', addr)[0]

    # 3. Strip trailing/leading whitespace
    addr = addr.strip()

    # 4. Strip punctuation (periods, commas, etc.)
    addr = re.sub(r'[.,]', '', addr)

    # 5. Collapse multiple spaces
    addr = re.sub(r'\s+', ' ', addr)

    # 6. Normalize casing (Title Case)
    addr = addr.title()

    # 7. Expand abbreviations (case-insensitive, end of string)
    for pattern, replacement in ABBREVIATION_MAP.items():
        addr = re.sub(pattern, replacement, addr, flags=re.IGNORECASE)

    # 8. Preserve directionals in uppercase
    # Match directionals at the start (before first space) and uppercase them
    addr = re.sub(r'^([nNsSeEwW]{1,2})\s', lambda m: m.group(1).upper() + ' ', addr)

    # 9. Final strip
    addr = addr.strip()

    return addr
