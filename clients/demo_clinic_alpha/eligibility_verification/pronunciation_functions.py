"""
DEAD CODE - Cartesia SSML pronunciation formatting functions.

These functions generate Cartesia-specific <spell> and <break> tags for TTS pronunciation.
Kept for reference in case we need to reintroduce explicit pronunciation control.

Originally used in EligibilityVerificationFlow to format phone numbers, reference numbers, and patient fields
for clearer speech output. Removed in favor of simpler LLM-driven pronunciation guidance.

To reuse: Import these functions and call _format_speech_fields() in flow __init__,
then reference the '_speech' suffixed fields in prompts.
"""

import re


def _format_phone(phone: str) -> str:
    """Format phone number with <spell> tags and breaks.

    Args:
        phone: Phone number string (e.g., "+15551234567")

    Returns:
        Formatted string with Cartesia SSML tags for clear pronunciation.
        Example: "<spell>(555)</spell><break time=\"200ms\"/><spell>123</spell><break time=\"200ms\"/><spell>4567</spell>"
    """
    # Remove +1 prefix and any formatting
    cleaned = re.sub(r'[^\d]', '', phone)
    if cleaned.startswith('1'):
        cleaned = cleaned[1:]

    if len(cleaned) == 10:
        # Format: (123) <break> 456 <break> 7890
        return (
            f"<spell>({cleaned[:3]})</spell><break time=\"200ms\"/>"
            f"<spell>{cleaned[3:6]}</spell><break time=\"200ms\"/>"
            f"<spell>{cleaned[6:]}</spell>"
        )
    return f"<spell>{phone}</spell>"


def _format_reference_number(ref_number: str) -> str:
    """Format reference/authorization number with <spell> tags and breaks.

    Reference numbers can be alphanumeric with various formats.
    This method groups characters in chunks of 3 for clarity with pauses between segments.

    Args:
        ref_number: Reference or authorization number string

    Returns:
        Formatted string with Cartesia SSML tags.
        Example: "<spell>ABC</spell><break time=\"200ms\"/><spell>123</spell>"
    """
    if not ref_number:
        return ""

    # Remove any whitespace or special characters for processing
    cleaned = re.sub(r'[^A-Za-z0-9]', '', ref_number)

    if not cleaned:
        return f"<spell>{ref_number}</spell>"

    # 5 or less: spell as one unit
    if len(cleaned) <= 5:
        return f"<spell>{cleaned}</spell>"

    # 6 or more: break into groups of 3
    chunks = [cleaned[i:i+3] for i in range(0, len(cleaned), 3)]
    formatted_chunks = [f"<spell>{chunk}</spell>" for chunk in chunks]
    return "<break time=\"200ms\"/>".join(formatted_chunks)


def _format_speech_fields(patient_data: dict) -> None:
    """Format patient data fields with Cartesia <spell> tags for pronunciation.

    This runs ONCE at flow initialization, adding '_speech' versions of fields
    that need to be spelled out. Zero runtime overhead during conversation.

    Args:
        patient_data: Dictionary of patient data. Modified in place to add '_speech' suffixed keys.

    Fields formatted:
        - insurance_phone → insurance_phone_speech
        - supervisor_phone → supervisor_phone_speech
        - insurance_member_id → insurance_member_id_speech
        - provider_npi → provider_npi_speech
        - cpt_code → cpt_code_speech
    """
    # Phone numbers: spell with breaks between segments
    insurance_phone = patient_data.get('insurance_phone')
    if insurance_phone:
        patient_data['insurance_phone_speech'] = _format_phone(insurance_phone)

    supervisor_phone = patient_data.get('supervisor_phone')
    if supervisor_phone:
        patient_data['supervisor_phone_speech'] = _format_phone(supervisor_phone)

    # Member ID: spell out alphanumeric
    member_id = patient_data.get('insurance_member_id')
    if member_id:
        patient_data['insurance_member_id_speech'] = f"<spell>{member_id}</spell>"

    # NPI: spell with breaks every 3 digits
    npi = patient_data.get('provider_npi')
    if npi:
        if len(npi) == 10 and npi.isdigit():
            patient_data['provider_npi_speech'] = (
                f"<spell>{npi[:3]}</spell><break time=\"150ms\"/>"
                f"<spell>{npi[3:6]}</spell><break time=\"150ms\"/>"
                f"<spell>{npi[6:]}</spell>"
            )
        else:
            patient_data['provider_npi_speech'] = f"<spell>{npi}</spell>"

    # CPT code: spell entire code
    cpt = patient_data.get('cpt_code')
    if cpt:
        patient_data['cpt_code_speech'] = f"<spell>{cpt}</spell>"
