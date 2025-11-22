import { useState } from "react"
import { MoreHorizontal, Phone, Trash2, Pencil } from "lucide-react"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Patient } from "@/types"
import { startCall, deletePatient } from "@/api"
import { EditPatientSheet } from "./edit-patient-sheet"
import { getSelectedWorkflow, getCurrentWorkflowSchema } from "@/lib/auth"

interface PatientActionsCellProps {
  patient: Patient
  onActionComplete?: () => void
}

export function PatientActionsCell({ 
  patient, 
  onActionComplete 
}: PatientActionsCellProps) {
  const [isLoading, setIsLoading] = useState(false)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [showEditSheet, setShowEditSheet] = useState(false)

  const handleStartCall = async (e: React.MouseEvent) => {
    e.stopPropagation()

    const workflow = getSelectedWorkflow()
    if (!workflow) {
      toast.error("No workflow selected")
      return
    }

    // Get phone number from flat fields - could be insurance_phone or patient_phone depending on workflow
    const phoneNumber = patient.insurance_phone || patient.patient_phone || patient.phone_number
    if (!phoneNumber) {
      toast.error("Missing phone number")
      return
    }

    try {
      setIsLoading(true)
      await startCall(patient.patient_id, phoneNumber, workflow)
      toast.success(`Call started for ${patient.patient_name}`)
      onActionComplete?.()
    } catch (err: any) {
      console.error("Error starting call:", err)
      const errorMsg = err.response?.data?.detail || "Failed to start call"
      toast.error(errorMsg)
    } finally {
      setIsLoading(false)
    }
  }

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    setShowDeleteDialog(true)
  }

  const handleDeleteConfirm = async () => {
    try {
      setIsLoading(true)
      await deletePatient(patient.patient_id)
      toast.success(`${patient.patient_name} deleted`)
      onActionComplete?.()
    } catch (err) {
      console.error("Error deleting patient:", err)
      toast.error("Failed to delete patient")
    } finally {
      setIsLoading(false)
      setShowDeleteDialog(false)
    }
  }

  return (
    <>
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
            <>
              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  setShowEditSheet(true)
                }}
                disabled={isLoading}
              >
                <Pencil className="mr-2 h-4 w-4" />
                Edit Patient
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={handleStartCall}
                disabled={isLoading}
              >
                <Phone className="mr-2 h-4 w-4" />
                Start Call
              </DropdownMenuItem>
            </>
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
            onClick={handleDeleteClick}
            disabled={isLoading}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Delete Patient
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {patient.patient_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the patient record.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <EditPatientSheet
        patient={patient}
        open={showEditSheet}
        onOpenChange={setShowEditSheet}
        onSave={onActionComplete}
      />
    </>
  )
}