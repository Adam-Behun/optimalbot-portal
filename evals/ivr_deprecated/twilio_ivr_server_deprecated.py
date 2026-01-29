import os

from flask import Flask, request
from loguru import logger
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__)

# Get transfer number from environment or default
TRANSFER_NUMBER = os.getenv("TEST_TRANSFER_NUMBER", "+15165667132")


@app.route("/voice", methods=['POST'])
def main_menu():
    """Main IVR menu - simulates insurance company"""
    logger.info(f"📞 Call from: {request.values.get('From')}")

    response = VoiceResponse()
    with response.gather(
        num_digits=1,
        action='/main-menu-handler',
        method="POST",
        timeout=5
    ) as g:
        g.say(
            "Thank you for calling Blue Cross Insurance. "
            "For member services, press 1. "
            "For provider services, press 2. "
            "For claims, press 3. "
            "To hear this menu again, press 9.",
            voice="Polly.Joanna"
        )

    # If no input, repeat
    response.redirect('/voice')
    return str(response)


@app.route("/main-menu-handler", methods=['POST'])
def main_menu_handler():
    """Handle main menu selection"""
    digit = request.values.get('Digits')
    logger.info(f"🔢 Main menu: User pressed {digit}")

    response = VoiceResponse()

    if digit == '1':
        # Member services → sub-menu
        response.redirect('/member-services')
    elif digit == '2':
        # Provider services → eligibility (our goal!)
        response.redirect('/provider-services')
    elif digit == '3':
        # Claims department
        response.say("Transferring to claims department.", voice="Polly.Joanna")
        response.redirect('/human-rep')
    elif digit == '9':
        # Repeat menu
        response.redirect('/voice')
    else:
        response.say("Invalid selection.", voice="Polly.Joanna")
        response.redirect('/voice')

    return str(response)


@app.route("/member-services", methods=['POST'])
def member_services():
    """Member services sub-menu"""
    logger.info("📋 Member services menu")

    response = VoiceResponse()
    with response.gather(
        num_digits=1,
        action='/member-services-handler',
        method="POST",
        timeout=5
    ) as g:
        g.say(
            "Member services. "
            "For eligibility, press 1. "
            "For benefits, press 2. "
            "To speak with a representative, press 0. "
            "To return to main menu, press 9.",
            voice="Polly.Joanna"
        )

    response.redirect('/member-services')
    return str(response)


@app.route("/member-services-handler", methods=['POST'])
def member_services_handler():
    """Handle member services selection"""
    digit = request.values.get('Digits')
    logger.info(f"🔢 Member services: User pressed {digit}")

    response = VoiceResponse()

    if digit in ['1', '2']:
        # Eligibility or benefits → human rep
        response.say("Connecting you to a representative.", voice="Polly.Joanna")
        response.redirect('/human-rep')
    elif digit == '0':
        # Direct to rep
        response.redirect('/human-rep')
    elif digit == '9':
        response.redirect('/voice')
    else:
        response.say("Invalid selection.", voice="Polly.Joanna")
        response.redirect('/member-services')

    return str(response)


@app.route("/provider-services", methods=['POST'])
def provider_services():
    """Provider services sub-menu (this is the correct path!)"""
    logger.info("🏥 Provider services menu")

    response = VoiceResponse()
    with response.gather(
        num_digits=1,
        action='/provider-services-handler',
        method="POST",
        timeout=5
    ) as g:
        g.say(
            "Provider services. "
            "For eligibility verification, press 1. "
            "For claims status, press 2. "
            "For authorizations, press 3. "
            "To speak with a representative, press 0.",
            voice="Polly.Joanna"
        )

    response.redirect('/provider-services')
    return str(response)


@app.route("/provider-services-handler", methods=['POST'])
def provider_services_handler():
    """Handle provider services selection"""
    digit = request.values.get('Digits')
    logger.info(f"🔢 Provider services: User pressed {digit}")

    response = VoiceResponse()

    if digit == '1':
        # Eligibility verification → SUCCESS!
        logger.info("✅ Correct path: Eligibility verification selected")
        response.say("Connecting you to eligibility verification.", voice="Polly.Joanna")
        response.redirect('/human-rep')
    elif digit in ['2', '3']:
        # Other departments
        response.say("Transferring.", voice="Polly.Joanna")
        response.redirect('/human-rep')
    elif digit == '0':
        response.redirect('/human-rep')
    else:
        response.say("Invalid selection.", voice="Polly.Joanna")
        response.redirect('/provider-services')

    return str(response)


@app.route("/human-rep", methods=['POST'])
def human_rep():
    """Transfer to real human (your number)"""
    logger.info(f"👤 Transferring to human representative: {TRANSFER_NUMBER}")

    response = VoiceResponse()
    response.say(
        "Connecting you to a representative. Please hold.",
        voice="Polly.Joanna"
    )

    # Dial your actual phone number
    # Twilio will bridge the bot call with your phone
    _dial = response.dial(TRANSFER_NUMBER, timeout=30, caller_id="+15165853321", action="/dial-status")

    return str(response)


@app.route("/dial-status", methods=['POST'])
def dial_status():
    """Handle dial outcome - end call gracefully regardless of result"""
    dial_status = request.values.get('DialCallStatus')
    logger.info(f"📞 Dial completed with status: {dial_status}")

    response = VoiceResponse()

    if dial_status == 'completed':
        # Human answered and conversation happened - call ends naturally
        logger.info("✅ Human conversation completed - ending call")
        response.say("Thank you for calling. Goodbye.", voice="Polly.Joanna")
    elif dial_status == 'no-answer':
        # No answer - go to voicemail
        logger.info("⏰ No answer - redirecting to voicemail")
        response.redirect('/voicemail')
    else:
        # Busy, failed, or canceled - just end the call
        logger.info(f"❌ Dial unsuccessful ({dial_status}) - ending call")
        response.say("The representative is unavailable. Goodbye.", voice="Polly.Joanna")

    response.hangup()
    return str(response)


@app.route("/voicemail", methods=['POST'])
def voicemail():
    """Simulate voicemail system"""
    logger.info("📧 Voicemail system")

    response = VoiceResponse()
    response.say(
        "You have reached the insurance verification department. "
        "Our office hours are Monday through Friday, 9 AM to 5 PM. "
        "Please leave a message after the tone, and we will return your call.",
        voice="Polly.Joanna"
    )
    response.play("http://com.twilio.sounds.music.s3.amazonaws.com/MARKOVICHAMP-Borghestral.mp3")
    response.record(timeout=30, maxLength=60)
    response.hangup()

    return str(response)


@app.route("/health", methods=['GET'])
def health():
    """Health check endpoint"""
    return {"status": "ok", "transfer_number": TRANSFER_NUMBER}


if __name__ == "__main__":
    port = int(os.getenv("TEST_SERVER_PORT", 5001))
    logger.info(f"🚀 Starting IVR test server on port {port}")
    logger.info(f"📞 Will transfer calls to: {TRANSFER_NUMBER}")
    logger.info("")
    logger.info("Setup instructions:")
    logger.info("1. Run: ngrok http 5001")
    logger.info("2. Copy ngrok URL (e.g., https://abc123.ngrok.io)")
    logger.info("3. Configure Twilio number to point to: https://abc123.ngrok.io/voice")
    logger.info("")
    app.run(debug=False, port=port, host='0.0.0.0')
