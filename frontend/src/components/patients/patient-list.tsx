import { useEffect, useState, useMemo, useCallback } from 'react';
import { getPatients, startCall, deletePatient } from '@/api';
import { Patient } from '@/types';
import { DataTable } from './data-table';
import { createColumns } from './columns';
import { PatientDetailSheet } from './patient-detail-sheet';
import { Button } from '@/components/ui/button';
import { Phone, Trash2, RefreshCw } from 'lucide-react';

export default function PatientList() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPatients, setSelectedPatients] = useState<Patient[]>([]);
  const [bulkActionLoading, setBulkActionLoading] = useState(false);
  
  // Sheet state
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
      alert('No eligible patients selected. Only patients with "Not Started" status and valid phone numbers can have calls initiated.');
      return;
    }

    const missingPhone = selectedPatients.filter(
      p => p.call_status === 'Not Started' && !p.insurance_phone
    );

    if (missingPhone.length > 0) {
      const proceed = window.confirm(
        `${missingPhone.length} patient(s) are missing phone numbers and will be skipped. Continue with ${eligiblePatients.length} patient(s)?`
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
    
    alert(
      `Bulk call initiation complete.\nSuccess: ${successCount}\nFailed: ${failCount}`
    );
  };

  const handleBulkDelete = async () => {
    if (selectedPatients.length === 0) {
      alert('No patients selected.');
      return;
    }

    const confirmed = window.confirm(
      `Are you sure you want to delete ${selectedPatients.length} patient(s)? This action cannot be undone.`
    );
    
    if (!confirmed) return;

    setBulkActionLoading(true);
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
    
    alert(
      `Bulk delete complete.\nSuccess: ${successCount}\nFailed: ${failCount}`
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground">Loading patients...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <p className="text-destructive">{error}</p>
        <Button onClick={loadPatients} variant="outline">
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Patients</h1>
          <p className="text-muted-foreground mt-1">
            {patients.length} patient(s) with pending authorization
          </p>
        </div>
        <Button onClick={loadPatients} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Bulk Actions Bar */}
      {selectedPatients.length > 0 && (
        <div className="flex items-center gap-2 p-4 bg-muted rounded-lg">
          <span className="text-sm font-medium">
            {selectedPatients.length} patient(s) selected
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

      {/* Data Table */}
      {patients.length === 0 ? (
        <div className="text-center py-12 border rounded-lg bg-card">
          <p className="text-muted-foreground">No patients with pending authorization</p>
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={patients}
          onRowClick={handleRowClick}
          onSelectionChange={handleSelectionChange}
        />
      )}

      {/* Patient Detail Sheet */}
      <PatientDetailSheet
        patient={selectedPatient}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
      />
    </div>
  );
}