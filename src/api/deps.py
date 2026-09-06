"""
deps.py — Request helpers and dependencies for API endpoints.
"""

def extract_phone(customer: dict) -> str:
    """Extracts and normalizes phone number from Interakt customer payload."""
    country_code = str(customer.get("country_code", "")).replace("+", "").strip()
    phone_num = str(customer.get("phone_number") or customer.get("phoneNumber") or "").strip().replace("+", "")
    
    # If standard 10-digit number, prepend country code (defaulting to 91)
    if len(phone_num) == 10:
        return (country_code or "91") + phone_num

    if phone_num.startswith(country_code) and country_code:
        return phone_num
    elif country_code and phone_num:
        return country_code + phone_num
    return phone_num or str(customer.get("channel_phone_number", ""))
