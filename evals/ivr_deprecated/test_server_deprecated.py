"""
Test IVR system to validate voicemail/IVR detection and navigation
Simulates real insurance company phone system
"""
from flask import Flask, request
from loguru import logger
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__)

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
    logger.info("👤 Transferring to human representative: +15165667132")
    
    response = VoiceResponse()
    response.say(
        "Connecting you to a representative. Please hold.",
        voice="Polly.Joanna"
    )
    
    # Dial your actual phone number
    response.dial("+15165667132", timeout=30)
    
    # If no answer, go to voicemail
    response.say("The representative is unavailable.", voice="Polly.Joanna")
    response.redirect('/voicemail')
    
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
    
    return str(response)


if __name__ == "__main__":
    app.run(debug=True, port=5001)