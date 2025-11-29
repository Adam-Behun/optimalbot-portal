# Patient Intake Workflow - Implementation Tasks

Implement the following 3 changes. Each task includes the exact files to modify and the specific changes required.

---

## Task 1: Hide First Name and Last Name in Detail View

**Goal:** In the call detail sheet, show only `Patient Name` - not the redundant `First Name` and `Last Name` fields.

**File to modify:** `frontend/src/components/workflows/patient_intake/PatientIntakeCallList.tsx`

**Change:** At line 261, filter out `first_name` and `last_name` from the detail view:

```tsx
// Before (line 261):
{allFields.map(field => (

// After:
{allFields.filter(field => !['first_name', 'last_name'].includes(field.key)).map(field => (
```

---

## Task 2: Add Time Type Support and Fix Datetime Display

**Goal:** Add `'time'` as a supported field type and ensure datetime fields display in the user's local timezone.

### File 1: `frontend/src/types.ts`

**Change:** At line 22, add `'time'` to the type union:

```typescript
// Before:
type: 'string' | 'date' | 'datetime' | 'phone' | 'select' | 'text';

// After:
type: 'string' | 'date' | 'datetime' | 'time' | 'phone' | 'select' | 'text';
```

### File 2: `frontend/src/components/workflows/patient_intake/PatientIntakeCallList.tsx`

**Change:** Update the `formatValue` function (lines 30-40) to handle `time` type and improve datetime display:

```typescript
function formatValue(value: unknown, field: SchemaField): string {
  if (value === null || value === undefined || value === '') return '-';
  switch (field.type) {
    case 'date':
      return new Date(value as string).toLocaleDateString();
    case 'datetime':
      // Display in user's local timezone with date and time
      return new Date(value as string).toLocaleString();
    case 'time':
      // For time-only fields stored as "HH:MM" or "10:30 AM"
      return String(value);
    default:
      return String(value);
  }
}
```

---

## Task 3: Update Schema - Change Appointment Time Type and Remove first_name/last_name from Schema

**Goal:** Update the workflow schema to use proper field types and remove redundant fields.

**File to modify:** `scripts/add_patient_intake_workflow.py`

**Changes:**

1. Change `appointment_time` from `"string"` to `"time"` (line 53)
2. Remove `first_name` and `last_name` field definitions entirely (lines 50-51) - these are internal fields that don't need schema entries since `patient_name` is computed from them

```python
# Updated patient_intake_workflow fields array (lines 37-61):
"patient_schema": {
    "fields": [
        # Appointment type (New Patient or Returning Patient)
        {"key": "appointment_type", "label": "Appointment Type", "type": "string", "required": False, "display_in_list": True, "display_order": 1, "computed": True},

        # Appointment info
        {"key": "appointment_date", "label": "Appointment Date", "type": "date", "required": False, "display_in_list": True, "display_order": 2, "computed": True},

        # Patient info
        {"key": "patient_name", "label": "Patient Name", "type": "string", "required": False, "display_in_list": True, "display_order": 3, "computed": True},
        {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False, "display_in_list": True, "display_order": 4, "computed": True},
        {"key": "phone_number", "label": "Phone Number", "type": "phone", "required": False, "display_in_list": True, "display_order": 5, "computed": True},

        # Detail view only fields
        {"key": "email", "label": "Email", "type": "string", "required": False, "display_in_list": False, "display_order": 6, "computed": True},
        {"key": "appointment_time", "label": "Appointment Time", "type": "time", "required": False, "display_in_list": False, "display_order": 7, "computed": True},
        {"key": "appointment_reason", "label": "Appointment Reason", "type": "string", "required": False, "display_in_list": False, "display_order": 8, "computed": True},

        # System fields
        {"key": "call_status", "label": "Call Status", "type": "string", "required": False, "display_in_list": False, "display_order": 9, "computed": True},
        {"key": "created_at", "label": "Created", "type": "datetime", "required": False, "display_in_list": False, "display_order": 10, "computed": True},
        {"key": "updated_at", "label": "Last Updated", "type": "datetime", "required": False, "display_in_list": False, "display_order": 11, "computed": True},
        {"key": "caller_phone_number", "label": "Caller Phone Number", "type": "phone", "required": False, "display_in_list": False, "display_order": 12, "computed": True}
    ]
}
```

**After modifying the script:** Run `python scripts/add_patient_intake_workflow.py` to update the database schema.

---

## Summary

| Task | File | Change |
|------|------|--------|
| 1 | `PatientIntakeCallList.tsx:261` | Filter out `first_name`, `last_name` from detail view |
| 2a | `types.ts:22` | Add `'time'` to SchemaField type union |
| 2b | `PatientIntakeCallList.tsx:30-40` | Add `case 'time':` to formatValue switch |
| 3 | `add_patient_intake_workflow.py` | Remove first_name/last_name fields, change appointment_time to type "time", renumber display_order |

---

## Notes

- The `first_name` and `last_name` values are still stored in the database by `flow_definition.py` - we're just not displaying them separately since `patient_name` shows the combined value
- Existing patient records are backwards compatible - the schema change only affects display
- Datetime fields already display in local timezone via `toLocaleString()` - no backend changes needed
