from __future__ import annotations

import re

import db


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d[\d\s-]{7,}$")
ACCOUNT_RE = re.compile(r"^ACC-[A-Z0-9-]+$", re.IGNORECASE)


def normalize_identifier(value: str | None) -> str:
    return (value or "").strip()


def is_email_identifier(value: str | None) -> bool:
    return bool(EMAIL_RE.match(normalize_identifier(value)))


def is_phone_identifier(value: str | None) -> bool:
    candidate = normalize_identifier(value)
    digits_only = re.sub(r"\D", "", candidate)
    return bool(PHONE_RE.match(candidate) and len(digits_only) >= 10)


def is_account_identifier(value: str | None) -> bool:
    return bool(ACCOUNT_RE.match(normalize_identifier(value)))


def is_identifier_message(value: str | None) -> bool:
    candidate = normalize_identifier(value)
    return is_email_identifier(candidate) or is_phone_identifier(candidate) or is_account_identifier(candidate)


def resolve_customer(
    identifier: str | None = None,
    phone_number: str | None = None,
    account_number: str | None = None,
) -> tuple[dict | None, str]:
    candidates = [
        normalize_identifier(identifier),
        normalize_identifier(phone_number),
        normalize_identifier(account_number).upper() if account_number else "",
    ]
    seen: set[str] = set()

    for candidate in candidates:
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)

        customer = db.get_customer_by_identifier(candidate)
        if customer:
            return customer, candidate

    return None, ""


def verification_prompt() -> str:
    return "Before we continue, please share your registered phone number, email address, or account number for verification."


def verification_retry_prompt() -> str:
    return "I could not verify that profile yet. Please share your registered phone number, email address, or account number again."


def verification_success_prompt(customer_name: str | None) -> str:
    if customer_name and customer_name != "Customer":
        return f"Hello {customer_name}! Your profile has been verified. Please tell me the problem you are facing."
    return "Your profile has been verified. Please tell me the problem you are facing."


def build_verification_state(
    customer_name: str = "Customer",
    phone_number: str = "",
    account_number: str = "",
    customer_identifier: str = "",
) -> dict:
    customer, matched_identifier = resolve_customer(
        identifier=customer_identifier,
        phone_number=phone_number,
        account_number=account_number,
    )

    if customer:
        return {
            "customer_name": customer["name"],
            "phone_number": customer["phone_no"],
            "account_number": customer["account_number"],
            "customer_identifier": matched_identifier or customer_identifier or customer["phone_no"],
            "is_verified": True,
            "verification_status": "verified",
            "verification_action": "wait",
        }

    return {
        "customer_name": customer_name,
        "phone_number": phone_number or "Unknown",
        "account_number": account_number or "Unknown",
        "customer_identifier": customer_identifier or phone_number or account_number or "",
        "is_verified": False,
        "verification_status": "pending",
        "verification_action": "wait",
    }
