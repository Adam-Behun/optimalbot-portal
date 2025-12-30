import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { SessionTable } from '../shared/SessionTable';
import { SessionDetailSheet } from '../shared/SessionDetailSheet';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { Session } from '@/types';
import { getSession } from '@/api';
import { useSessions, useDeleteSession, useDeleteSessions } from '@/hooks/useSessions';
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

const WORKFLOW = 'lab_results';

// Workflow-specific patient fields for lab_results
const LAB_RESULTS_FIELDS = [
  { key: 'test_type', label: 'Test Type' },
  { key: 'test_date', label: 'Test Date' },
  { key: 'ordering_physician', label: 'Ordering Physician' },
  { key: 'results_status', label: 'Results Status' },
  { key: 'results_summary', label: 'Results Summary' },
];

export function LabResultsCallList() {
  const { data: sessions = [], isLoading, error, refetch } = useSessions(WORKFLOW);
  const deleteSessionMutation = useDeleteSession(WORKFLOW);
  const deleteSessionsMutation = useDeleteSessions(WORKFLOW);

  // Sheet states
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);

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
      await deleteSessionMutation.mutateAsync(sessionToDelete.session_id);
      toast.success('Call record deleted');
      setDeleteDialogOpen(false);
      setSessionToDelete(null);
    } catch {
      toast.error('Failed to delete call record');
    }
  };

  const handleDeleteSessions = async (selectedSessions: Session[]) => {
    const sessionIds = selectedSessions.map(s => s.session_id);
    const result = await deleteSessionsMutation.mutateAsync(sessionIds);
    toast.success(`Deleted: ${result.successCount} success, ${result.failCount} failed`);
  };

  if (error) {
    return (
      <WorkflowLayout workflowName={WORKFLOW} title="Calls">
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error instanceof Error ? error.message : 'Failed to load calls'}</p>
          <Button onClick={() => refetch()} variant="outline">
            Retry
          </Button>
        </div>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName={WORKFLOW}
      title="Calls"
      actions={
        <Button onClick={() => refetch()} variant="outline" size="sm">
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
          loading={isLoading}
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
        workflowFields={LAB_RESULTS_FIELDS}
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
