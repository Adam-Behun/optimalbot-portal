# Patient Intake Call Flow - Demo Clinic Beta

## Overview

This is a **dial-in workflow** for scheduling dental appointments at Demo Clinic Beta. The virtual assistant is warm, conversational, excited, friendly, and supportive throughout the entire call.

## Conversation Tone

- **Excited and welcoming** - Make the patient feel valued
- **Friendly and conversational** - Natural, human-like interactions
- **Supportive** - Help guide patients through the booking process
- **Professional** - Maintain professionalism while being personable

---

## Flow Diagram

```
┌─────────────────┐
│    Greeting     │
│  (Welcome Call) │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  New or Returning?      │
│  - New Patient          │──────────────────┐
│  - Returning Patient    │                  │
└────────┬────────────────┘                  │
         │                                   │
         │ (Returning)                       │ (New Patient)
         ▼                                   ▼
┌─────────────────────────┐    ┌─────────────────────────┐
│  Returning Patient      │    │  New Patient Service    │
│  Reason for Visit       │    │  Selection              │
│  - Regular Check-up     │    │  - Professional Teeth   │
│  - Pain/Issue           │    │    Whitening            │
└────────┬────────────────┘    │  - New Patient          │
         │                     │    Appointment          │
         │                     └────────┬────────────────┘
         │                              │
         └──────────────┬───────────────┘
                        │
                        ▼
              ┌─────────────────────────┐
              │  Date Selection         │
              │  "Which day works       │
              │   best for you?"        │
              └────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────────────┐
              │  Time Selection         │
              │  Check available slots  │
              │  Offer options          │
              └────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────────────┐
              │  Collect Patient Info   │
              │  - First Name           │
              │  - Last Name            │
              │  - Phone Number         │
              │  - Date of Birth        │
              │  - Email Address        │
              └────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────────────┐
              │  Confirm Appointment    │
              │  Review all details     │
              └────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────────────┐
              │  Closing                │
              │  Thank patient          │
              │  Provide confirmation   │
              └─────────────────────────┘
```

---

## Detailed Node Descriptions

### 1. Greeting Node

**Purpose:** Welcome the caller warmly and establish a friendly connection.

**What the assistant does:**
- Answers the call with enthusiasm
- Introduces themselves as a Virtual Assistant from Demo Clinic Beta
- Asks how they can help today

**Example script:**
> "Hello! Thank you so much for calling Demo Clinic Beta! This is your Virtual Assistant, and I'm so excited to help you today! Are you a new patient with us, or have you visited us before?"

**Transitions to:** New/Returning Patient determination

---

### 2. New or Returning Patient Node

**Purpose:** Determine if this is a new patient or returning patient.

**What the assistant does:**
- Listens for indication of new vs returning
- Routes accordingly

**Functions:**
- `set_new_patient` → Goes to New Patient Service Selection
- `set_returning_patient` → Goes to Returning Patient Reason

---

### 3a. New Patient Service Selection Node

**Purpose:** Understand what service the new patient is looking for.

**What the assistant does:**
- Asks what brings them to Demo Clinic Beta
- Offers two main options:
  1. Professional Teeth Whitening
  2. New Patient Appointment (general)

**Example script:**
> "That's wonderful! We're so happy to have you! What are you looking to do today? Are you interested in our Professional Teeth Whitening service, or would you like to schedule a New Patient Appointment?"

**Functions:**
- `select_teeth_whitening` → Goes to Date Selection (service: whitening)
- `select_new_patient_appointment` → Goes to Date Selection (service: new patient)

---

### 3b. Returning Patient Reason Node

**Purpose:** Understand why the returning patient is calling.

**What the assistant does:**
- Warmly welcomes them back
- Asks about the reason for their visit:
  1. Regular check-up
  2. Pain or specific issue

**Example script:**
> "Welcome back! It's great to hear from you again! What can we help you with today? Are you due for a regular check-up, or is there something specific bothering you, like any pain or discomfort?"

**Functions:**
- `select_checkup` → Goes to Date Selection (service: checkup)
- `select_pain_issue` → Goes to Date Selection (service: urgent)

---

### 4. Date Selection Node

**Purpose:** Find out which day works best for the patient.

**What the assistant does:**
- Asks for the patient's preferred date
- Accepts various date formats
- Validates the date is valid and in the future

**Example script:**
> "Perfect! Which day works best for you? Just let me know the date you have in mind!"

**Functions:**
- `check_availability` → Checks available times for that date, transitions to Time Selection

---

### 5. Time Selection Node

**Purpose:** Present available time slots and let patient choose.

**What the assistant does:**
- Retrieves available time slots for the selected date
- Presents options to the patient
- Confirms the selected time

**Example script:**
> "Great news! On [date], we have openings at 9:00 AM, 11:30 AM, and 2:00 PM. Which time works best for you?"

**Functions:**
- `select_time` → Confirms time and goes to Collect Patient Info

---

### 6. Collect Patient Information Node

**Purpose:** Gather all necessary patient information for the appointment.

**Information collected:**
1. First Name
2. Last Name
3. Phone Number
4. Date of Birth
5. Email Address

**What the assistant does:**
- Collects each piece of information conversationally
- Confirms spellings where necessary
- Maintains friendly tone throughout

**Example script:**
> "Wonderful! Let me just get a few details to book your appointment. What's your first name?"

**Functions:**
- `save_first_name` → Continue to last name
- `save_last_name` → Continue to phone
- `save_phone_number` → Continue to DOB
- `save_date_of_birth` → Continue to email
- `save_email` → Go to Confirmation

---

### 7. Confirmation Node

**Purpose:** Review all appointment details with the patient.

**What the assistant does:**
- Summarizes all collected information:
  - Patient name
  - Appointment type/service
  - Date and time
  - Contact information
- Asks for confirmation
- Allows corrections if needed

**Example script:**
> "Okay, let me confirm everything! I have you down for a [service type] appointment on [date] at [time]. Your contact information is [phone] and [email]. Does everything look correct?"

**Functions:**
- `confirm_appointment` → Save to database, go to Closing
- `correct_information` → Go back to relevant node for correction

---

### 8. Closing Node

**Purpose:** Thank the patient and end the call warmly.

**What the assistant does:**
- Confirms the appointment is booked
- Thanks the patient enthusiastically
- Provides any final information
- Ends the call warmly

**Example script:**
> "Your appointment is all set! We're so excited to see you on [date] at [time]! You'll receive a confirmation at [email]. Thank you so much for choosing Demo Clinic Beta! Have a wonderful day!"

**Functions:**
- `end_call` → Save transcript, end call

---

## Data Collected

| Field | Description | Required |
|-------|-------------|----------|
| patient_type | "new" or "returning" | Yes |
| service_type | "whitening", "new_patient", "checkup", "urgent" | Yes |
| appointment_date | Selected date | Yes |
| appointment_time | Selected time slot | Yes |
| first_name | Patient's first name | Yes |
| last_name | Patient's last name | Yes |
| phone_number | Contact phone | Yes |
| date_of_birth | Patient DOB | Yes |
| email | Contact email | Yes |

---

## Error Handling

- **Invalid date:** Politely ask for a valid future date
- **No availability:** Suggest alternative dates
- **Unclear response:** Ask for clarification in a friendly way
- **Technical issues:** Apologize and offer to retry

---

## Notes

- The assistant should always maintain an upbeat, friendly tone
- Use the patient's name once collected to personalize the conversation
- Keep responses concise but warm
- This is a dial-in workflow - the patient is calling us
