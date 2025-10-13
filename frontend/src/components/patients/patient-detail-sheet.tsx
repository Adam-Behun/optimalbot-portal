import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import PatientDetail from "@/components/PatientDetail"
import { Patient } from "@/types"

interface PatientDetailSheetProps {
  patient: Patient | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function PatientDetailSheet({
  patient,
  open,
  onOpenChange,
}: PatientDetailSheetProps) {
  if (!patient) return null

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{patient.patient_name}</SheetTitle>
          <SheetDescription>
            Patient ID: {patient.patient_id}
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6">
          <PatientDetail patientId={patient.patient_id} />
        </div>
      </SheetContent>
    </Sheet>
  )
}