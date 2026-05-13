import re

PHONE_PATTERN = re.compile(r"^\+?[1-9]\d{7,14}$")


def validate_phone_number(value: str) -> str:
    normalized = value.strip().replace(" ", "").replace("-", "")
    if not PHONE_PATTERN.match(normalized):
        raise ValueError("Phone number must be E.164-like and contain 8 to 15 digits")
    return normalized


def validate_password_strength(value: str) -> None:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain an uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain a lowercase letter")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain a number")
    if not re.search(r"[^A-Za-z0-9]", value):
        raise ValueError("Password must contain a special character")
