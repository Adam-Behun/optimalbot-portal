import { useEffect, useState, useMemo, useCallback } from 'react';
import { getPatients, startCall, deletePatient } from '@/api';
import { Patient } from '@/types';
import { DataTable } from './data-table';
import { createColumns } from './columns';
import { PatientDetailSheet } from './patient-detail-sheet';
import { Button } from '@/components/ui/button';
import { Phone, Trash2, RefreshCw } from 'lucide-react';
import { toast } from "sonner";
import { Navigation } from "@/components/Navigation";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export default function PatientList() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPatients, setSelectedPatients] = useState<Patient[]>([]);
  const [bulkActionLoading, setBulkActionLoading] = useState(false);
  const [showBulkDeleteDialog, setShowBulkDeleteDialog] = useState(false);

  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const loadPatients = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getPatients();
      setPatients(data);
    } catch (err) {
      setError('Failed to load patients');
      console.error('Error loading patients:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const columns = useMemo(
    () => createColumns({ onActionComplete: loadPatients }),
    [loadPatients]
  );

  useEffect(() => {
    loadPatients();
  }, []);

  // ✅ Auto-refresh when calls are active
  useEffect(() => {
    const hasActiveCalls = patients.some(p => p.call_status === "In Progress");
    
    if (hasActiveCalls) {
      const interval = setInterval(() => {
        loadPatients();
      }, 3000);
      
      return () => clearInterval(interval);
    }
  }, [patients, loadPatients]);

  const handleRowClick = useCallback((patient: Patient) => {
    setSelectedPatient(patient);
    setSheetOpen(true);
  }, []);

  const handleSelectionChange = useCallback((selected: Patient[]) => {
    setSelectedPatients(selected);
  }, []);

  const handleBulkStartCalls = async () => {
    const eligiblePatients = selectedPatients.filter(
      p => p.call_status === 'Not Started' && p.insurance_phone
    );

    if (eligiblePatients.length === 0) {
      toast.warning("No eligible patients selected");  // ✅ Changed
      return;
    }

    const missingPhone = selectedPatients.filter(
      p => p.call_status === 'Not Started' && !p.insurance_phone
    );

    if (missingPhone.length > 0) {
      const proceed = window.confirm(
        `${missingPhone.length} patient(s) missing phone numbers. Continue with ${eligiblePatients.length}?`
      );
      if (!proceed) return;
    }

    const confirmed = window.confirm(
      `Start calls for ${eligiblePatients.length} patient(s)?`
    );
    
    if (!confirmed) return;

    setBulkActionLoading(true);
    let successCount = 0;
    let failCount = 0;

    for (const patient of eligiblePatients) {
      try {
        await startCall(patient.patient_id, patient.insurance_phone);
        successCount++;
      } catch (err) {
        console.error(`Error starting call for ${patient.patient_name}:`, err);
        failCount++;
      }
    }

    setBulkActionLoading(false);
    await loadPatients();
    
    toast.success(`Calls started: ${successCount} success, ${failCount} failed`);  // ✅ Changed
  };

  const handleBulkDelete = () => {
    if (selectedPatients.length === 0) {
      toast.warning("No patients selected");
      return;
    }
    setShowBulkDeleteDialog(true);
  };

  const handleBulkDeleteConfirm = async () => {
    setBulkActionLoading(true);
    setShowBulkDeleteDialog(false);
    let successCount = 0;
    let failCount = 0;

    for (const patient of selectedPatients) {
      try {
        await deletePatient(patient.patient_id);
        successCount++;
      } catch (err) {
        console.error(`Error deleting ${patient.patient_name}:`, err);
        failCount++;
      }
    }

    setBulkActionLoading(false);
    await loadPatients();
    
    toast.success(`Deleted: ${successCount} success, ${failCount} failed`);  // ✅ Changed
  };

  if (loading) {
    return (
      <>
        <Navigation />
        <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
          <p className="text-muted-foreground">Loading patients...</p>
        </div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <Navigation />
        <div className="flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={loadPatients} variant="outline">
            Retry
          </Button>
        </div>
      </>
    );
  }

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Patient List</h1>
            <p className="text-muted-foreground mt-1">
              {patients.length} patient(s)
            </p>
          </div>
          <Button onClick={loadPatients} variant="outline" size="sm">
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>

      {selectedPatients.length > 0 && (
        <div className="flex items-center gap-2 p-4 bg-muted rounded-lg">
          <span className="text-sm font-medium">
            {selectedPatients.length} selected
          </span>
          <div className="flex gap-2 ml-auto">
            <Button
              onClick={handleBulkStartCalls}
              disabled={bulkActionLoading}
              variant="default"
              size="sm"
            >
              <Phone className="mr-2 h-4 w-4" />
              Start Calls
            </Button>
            <Button
              onClick={handleBulkDelete}
              disabled={bulkActionLoading}
              variant="destructive"
              size="sm"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete Selected
            </Button>
          </div>
        </div>
      )}

      {patients.length === 0 ? (
        <div className="text-center py-12 border rounded-lg bg-card">
          <p className="text-muted-foreground">No patients found</p>
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={patients}
          onRowClick={handleRowClick}
          onSelectionChange={handleSelectionChange}
        />
      )}

      <PatientDetailSheet
        patient={selectedPatient}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
      />

      <AlertDialog open={showBulkDeleteDialog} onOpenChange={setShowBulkDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {selectedPatients.length} patient(s)?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the selected patient records.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      </div>
    </>
  );
}