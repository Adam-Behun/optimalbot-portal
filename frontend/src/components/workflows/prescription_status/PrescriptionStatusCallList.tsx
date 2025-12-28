import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { SessionTable } from '../shared/SessionTable';
import { SessionDetailSheet } from '../shared/SessionDetailSheet';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { Session } from '@/types';
import { getSessions, getSession, deleteSession } from '@/api';
import { RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

// Workflow-specific patient fields for prescription_status
const PRESCRIPTION_STATUS_FIELDS = [
  { key: 'medication_name', label: 'Medication' },
  { key: 'dosage', label: 'Dosage' },
  { key: 'refills_remaining', label: 'Refills Remaining' },
  { key: 'pharmacy_name', label: 'Pharmacy' },
  { key: 'prescription_status', label: 'Status' },
];

export function PrescriptionStatusCallList() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Sheet states
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);

  const loadSessions = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getSessions('prescription_status');
      setSessions(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load calls');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Poll only active sessions
  useEffect(() => {
    const activeSessionIds = sessions
      .filter(s => s.status === 'starting' || s.status === 'running')
      .map(s => s.session_id);

    if (activeSessionIds.length === 0) return;

    const interval = setInterval(async () => {
      for (const sessionId of activeSessionIds) {
        try {
          const updatedSession = await getSession(sessionId);
          setSessions(prev => prev.map(s =>
            s.session_id === sessionId ? updatedSession : s
          ));
        } catch {
          // Session fetch failed, skip
        }
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [sessions]);

  const handleViewSession = async (session: Session) => {
    try {
      const sessionData = await getSession(session.session_id);
      setSelectedSession(sessionData);
      setDetailSheetOpen(true);
    } catch {
      toast.error('Failed to load call details');
    }
  };

  const handleDeleteSessionSingle = (session: Session) => {
    setSessionToDelete(session);
    setDeleteDialogOpen(true);
  };

  const confirmDelete = async () => {
    if (!sessionToDelete) return;

    try {
      await deleteSession(sessionToDelete.session_id);
      toast.success('Call record deleted');
      setDeleteDialogOpen(false);
      setSessionToDelete(null);
      await loadSessions();
    } catch {
      toast.error('Failed to delete call record');
    }
  };

  const handleDeleteSessions = async (selectedSessions: Session[]) => {
    let successCount = 0;
    let failCount = 0;

    for (const session of selectedSessions) {
      try {
        await deleteSession(session.session_id);
        successCount++;
      } catch {
        failCount++;
      }
    }

    await loadSessions();
    toast.success(`Deleted: ${successCount} success, ${failCount} failed`);
  };

  if (error) {
    return (
      <WorkflowLayout workflowName="prescription_status" title="Calls">
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={loadSessions} variant="outline">
            Retry
          </Button>
        </div>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName="prescription_status"
      title="Calls"
      actions={
        <Button onClick={loadSessions} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="text-muted-foreground">
          {sessions.length} call(s)
        </p>
        <SessionTable
          sessions={sessions}
          loading={loading}
          onRowClick={handleViewSession}
          onViewSession={handleViewSession}
          onDeleteSession={handleDeleteSessionSingle}
          onDeleteSessions={handleDeleteSessions}
        />
      </div>

      {/* Call Detail Sheet */}
      <SessionDetailSheet
        session={selectedSession}
        open={detailSheetOpen}
        onOpenChange={setDetailSheetOpen}
        workflowFields={PRESCRIPTION_STATUS_FIELDS}
      />

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Call Record?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete this call record.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </WorkflowLayout>
  );
}
