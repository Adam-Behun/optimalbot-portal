"""
SMS webhook handler for text conversations.

Minimal implementation to receive SMS replies and process through TextConversation.
"""

from fastapi import APIRouter, Request, HTTPException, Form
from loguru import logger
from typing import Optional

from backend.models import get_async_patient_db

router = APIRouter()


@router.post("/sms-webhook/inbound")
async def handle_inbound_sms(
    request: Request,
    From: str = Form(...),  # Twilio sends as "From"
    Body: str = Form(...),  # Twilio sends as "Body"
    To: Optional[str] = Form(None),
):
    """
    Receive incoming SMS from Twilio webhook.

    Twilio sends form-encoded data with:
    - From: sender phone number (e.g., +14155551234)
    - Body: message content
    - To: your Twilio number
    """
    # Normalize phone number to digits only
    phone_digits = "".join(c for c in From if c.isdigit())
    if phone_digits.startswith("1") and len(phone_digits) == 11:
        phone_digits = phone_digits[1:]  # Strip country code

    logger.info(f"SMS received from ...{phone_digits[-4:]}: {Body[:50]}...")

    try:
        db = get_async_patient_db()

        # Find patient by phone number with text conversation enabled
        patient = await db.find_patient_by_phone_for_text(phone_digits)

        if not patient:
            logger.warning(f"No text-enabled patient found for phone ...{phone_digits[-4:]}")
            # Return TwiML response for unknown sender
            return _twiml_response(
                "Sorry, I don't have a record of this number. "
                "Please call our office if you need assistance."
            )

        patient_id = str(patient["_id"])
        organization_id = patient.get("organization_id")

        # Load or create text conversation
        from clients.demo_clinic_alpha.patient_scheduling.text_conversation import TextConversation

        conv_state = patient.get("text_conversation_state")
        if conv_state:
            text_conv = TextConversation.from_dict(conv_state)
        else:
            # Create new conversation with patient context
            text_conv = TextConversation(
                patient_id=patient_id,
                organization_id=organization_id,
                organization_name=patient.get("organization_name", "Demo Clinic Alpha"),
                initial_context={
                    "first_name": patient.get("first_name"),
                    "last_name": patient.get("last_name"),
                    "phone_number": phone_digits,
                    "email": patient.get("email"),
                    "appointment_date": patient.get("appointment_date"),
                    "appointment_time": patient.get("appointment_time"),
                },
            )

        # Process the message
        response = await text_conv.process_message(Body)

        # Save updated conversation state
        await db.update_patient(
            patient_id,
            {"text_conversation_state": text_conv.to_dict()},
            organization_id,
        )

        logger.info(f"SMS response to ...{phone_digits[-4:]}: {response[:50]}...")

        # Return TwiML response
        return _twiml_response(response)

    except Exception as e:
        logger.exception(f"Error processing SMS: {e}")
        return _twiml_response(
            "Sorry, I'm having trouble right now. Please call our office."
        )


def _twiml_response(message: str):
    """Generate TwiML XML response for Twilio."""
    from fastapi.responses import Response
    import html

    # Escape message for XML
    safe_message = html.escape(message)

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe_message}</Message>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@router.post("/sms/send")
async def send_sms(
    request: Request,
    patient_id: str,
    message: str,
):
    """
    Send SMS to a patient (for testing/manual sends).

    In production, this would call Twilio's API.
    For now, just logs the message.
    """
    db = get_async_patient_db()
    patient = await db.get_patient(patient_id)

    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    phone_number = patient.get("phone_number")
    if not phone_number:
        raise HTTPException(status_code=400, detail="Patient has no phone number")

    # TODO: Integrate with Twilio
    # twilio_client.messages.create(
    #     to=phone_number,
    #     from_=TWILIO_PHONE_NUMBER,
    #     body=message,
    # )

    logger.info(f"SMS would be sent to {phone_number[-4:]}: {message[:50]}...")

    return {"status": "queued", "phone": f"...{phone_number[-4:]}", "message_preview": message[:50]}
