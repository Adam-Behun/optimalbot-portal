import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { getAdminCallDetail, AdminCallDetail as AdminCallDetailType } from '@/api';
import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react';
import { formatDatetime } from '@/lib/utils';
import { TranscriptViewer } from '@/components/workflows/shared/TranscriptViewer';
import { StatusBadge } from '@/components/ui/status-badge';
import { DetailRow } from '@/components/ui/detail-row';

export function AdminCallDetail() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<AdminCallDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    getAdminCallDetail(sessionId)
      .then(setData)
      .catch((err) => setError(err.message || 'Failed to load call details'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Call Details</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Call Details</h1>
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error || 'Call not found'}</p>
          <Button onClick={() => navigate('/admin/calls')} variant="outline">
            Back to Calls
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate('/admin/calls')}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-2xl font-bold">Call Details</h1>
        </div>
        <Button onClick={fetchData} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Call Information */}
        <Card>
          <CardHeader>
            <CardTitle>Call Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-0">
            <DetailRow label="Session ID" value={
              <span className="font-mono text-sm">{data.session_id}</span>
            } />
            <DetailRow label="Organization" value={data.organization_name} />
            <DetailRow label="Workflow" value={data.workflow} />
            <DetailRow label="Status" value={<StatusBadge status={data.status} />} />
            <DetailRow label="Call Type" value={data.call_type} />
            <DetailRow label="Call Reason" value={data.call_reason} />
            <DetailRow label="Caller Name" value={data.caller_name} />
            <DetailRow label="Caller Phone" value={data.caller_phone} />
            <DetailRow label="Called Phone" value={data.called_phone} />
            <DetailRow label="Routed To" value={data.routed_to} />
            <DetailRow label="Identity Verified" value={data.identity_verified ? 'Yes' : 'No'} />
            <DetailRow label="Created" value={formatDatetime(data.created_at)} />
            {data.completed_at && (
              <DetailRow label="Completed" value={formatDatetime(data.completed_at)} />
            )}
            {data.error_message && (
              <DetailRow label="Error" value={
                <span className="text-red-600">{data.error_message}</span>
              } />
            )}
          </CardContent>
        </Card>

        {/* Cost Breakdown */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Cost Breakdown</span>
              <span className="text-lg font-bold text-blue-600">
                ${data.total_cost_usd?.toFixed(4) ?? '0.0000'}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data.costs_breakdown && data.costs_breakdown.length > 0 ? (
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model</TableHead>
                      <TableHead className="text-right">Input</TableHead>
                      <TableHead className="text-right">Output</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.costs_breakdown.map((cost, idx) => (
                      <TableRow key={idx}>
                        <TableCell className="font-mono text-sm">{cost.model}</TableCell>
                        <TableCell className="text-right">{cost.input_tokens.toLocaleString()}</TableCell>
                        <TableCell className="text-right">{cost.output_tokens.toLocaleString()}</TableCell>
                        <TableCell className="text-right">${cost.cost_usd.toFixed(4)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <p className="text-muted-foreground text-center py-4">No cost data available</p>
            )}

            <div className="mt-4">
              <a
                href={data.langfuse_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-blue-600 hover:underline"
              >
                <ExternalLink className="h-4 w-4" />
                View in Langfuse
              </a>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Transcript */}
      {data.call_transcript?.messages && data.call_transcript.messages.length > 0 && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Call Transcript ({data.call_transcript.message_count} messages)</CardTitle>
          </CardHeader>
          <CardContent>
            <TranscriptViewer
              messages={data.call_transcript.messages.map(m => ({
                role: m.role as 'user' | 'assistant' | 'system',
                content: m.content,
                timestamp: m.timestamp || '',
                type: 'transcript' as const,
              }))}
              callerLabel="Caller"
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
