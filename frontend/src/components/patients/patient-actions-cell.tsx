import { useState } from "react"
import { MoreHorizontal, Phone, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Patient } from "@/types"
import { startCall, deletePatient } from "@/api"

interface PatientActionsCellProps {
  patient: Patient
  onActionComplete?: () => void
}

export function PatientActionsCell({ 
  patient, 
  onActionComplete 
}: PatientActionsCellProps) {
  const [isLoading, setIsLoading] = useState(false)

  const handleStartCall = async (e: React.MouseEvent) => {
    e.stopPropagation()

    if (!patient.insurance_phone) {
      alert("Insurance phone number is missing for this patient. Please update patient information.")
      return
    }

    try {
      setIsLoading(true)
      await startCall(patient.patient_id, patient.insurance_phone)
      alert(`Call started for ${patient.patient_name}`)
      onActionComplete?.()
    } catch (err: any) {
      console.error("Error starting call:", err)
      const errorMsg = err.response?.data?.detail || "Failed to start call. Please try again."
      alert(errorMsg)
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()

    if (!window.confirm(`Are you sure you want to delete ${patient.patient_name}?`)) {
      return
    }

    try {
      setIsLoading(true)
      await deletePatient(patient.patient_id)
      alert(`${patient.patient_name} deleted successfully`)
      onActionComplete?.()
    } catch (err) {
      console.error("Error deleting patient:", err)
      alert("Failed to delete patient. Please try again.")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
        <Button 
          variant="ghost" 
          className="h-8 w-8 p-0"
          disabled={isLoading}
        >
          <span className="sr-only">Open menu</span>
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuLabel>Actions</DropdownMenuLabel>
        <DropdownMenuSeparator />
        
        {patient.call_status === "Not Started" && (
          <DropdownMenuItem 
            onClick={handleStartCall}
            disabled={isLoading}
          >
            <Phone className="mr-2 h-4 w-4" />
            Start Call
          </DropdownMenuItem>
        )}
        
        {patient.call_status === "In Progress" && (
          <DropdownMenuItem disabled>
            <Phone className="mr-2 h-4 w-4" />
            Call in progress...
          </DropdownMenuItem>
        )}
        
        {patient.call_status === "Completed" && (
          <DropdownMenuItem disabled>
            <Phone className="mr-2 h-4 w-4" />
            ✓ Call completed
          </DropdownMenuItem>
        )}
        
        <DropdownMenuSeparator />
        
        <DropdownMenuItem 
          onClick={handleDelete}
          disabled={isLoading}
          className="text-destructive focus:text-destructive"
        >
          <Trash2 className="mr-2 h-4 w-4" />
          Delete Patient
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}