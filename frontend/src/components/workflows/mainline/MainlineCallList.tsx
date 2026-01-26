import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/ui/status-badge';
import { DetailRow } from '@/components/ui/detail-row';
import { TranscriptViewer } from '../shared/TranscriptViewer';
import { WorkflowLayout } from '../shared/WorkflowLayout';
import { Session, TranscriptMessage } from '@/types';
import { useSessions } from '@/hooks/useSessions';
import { formatDatetime } from '@/lib/utils';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import { RefreshCw } from 'lucide-react';

const WORKFLOW = 'mainline';

export function MainlineCallList() {
  const { data: sessions = [], isLoading, error, refetch } = useSessions(WORKFLOW);
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);

  const handleViewSession = (session: Session) => {
    setSelectedSession(session);
    if (session.call_transcript?.messages) {
      setTranscript(session.call_transcript.messages);
    } else {
      setTranscript([]);
    }
    setDetailSheetOpen(true);
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

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Call Type</TableHead>
                <TableHead>Caller</TableHead>
                <TableHead>Routed To</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                    Loading...
                  </TableCell>
                </TableRow>
              ) : sessions.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                    No calls yet
                  </TableCell>
                </TableRow>
              ) : (
                sessions.map((session) => (
                  <TableRow
                    key={session.session_id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => handleViewSession(session)}
                  >
                    <TableCell>{session.call_type || '-'}</TableCell>
                    <TableCell>{session.caller_name || session.caller_phone || '-'}</TableCell>
                    <TableCell>{session.routed_to || '-'}</TableCell>
                    <TableCell><StatusBadge status={session.status} /></TableCell>
                    <TableCell>{formatDatetime(session.created_at)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <Sheet open={detailSheetOpen} onOpenChange={setDetailSheetOpen}>
        <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{selectedSession?.caller_name || 'Call Details'}</SheetTitle>
            <SheetDescription>
              View call information and transcript
            </SheetDescription>
          </SheetHeader>

          {selectedSession && (
            <div className="mt-6 space-y-4">
              <div className="bg-card rounded-lg border p-4 space-y-3">
                <h3 className="text-lg font-semibold text-primary mb-3">
                  Call Information
                </h3>
                <div className="space-y-0">
                  <DetailRow label="Call Type" value={selectedSession.call_type} />
                  <DetailRow label="Call Reason" value={selectedSession.call_reason} />
                  <DetailRow label="Caller Name" value={selectedSession.caller_name} />
                  <DetailRow label="Caller Phone" value={selectedSession.caller_phone} />
                  <DetailRow label="Routed To" value={selectedSession.routed_to} />
                  <DetailRow label="Status" value={<StatusBadge status={selectedSession.status} />} />
                  <DetailRow label="Date" value={formatDatetime(selectedSession.created_at)} />
                  {selectedSession.completed_at && (
                    <DetailRow label="Completed" value={formatDatetime(selectedSession.completed_at)} />
                  )}
                </div>
              </div>

              {transcript.length > 0 ? (
                <div className="bg-card rounded-lg border p-4">
                  <h3 className="text-lg font-semibold text-primary mb-3">
                    Call Transcript
                  </h3>
                  <TranscriptViewer messages={transcript} callerLabel="Caller" />
                </div>
              ) : selectedSession.status === 'completed' ? (
                <div className="bg-card rounded-lg border p-4 text-center">
                  <h3 className="text-lg font-semibold text-primary mb-3">
                    Call Transcript
                  </h3>
                  <p className="text-muted-foreground">No transcript available for this call.</p>
                </div>
              ) : null}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </WorkflowLayout>
  );
}
