import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { getPatients } from '@/api';
import { Phone, CheckCircle, XCircle, Clock, PhoneForwarded, PhoneIncoming } from 'lucide-react';
import { WorkflowLayout } from '../shared/WorkflowLayout';

const WORKFLOW = 'mainline';

export function MainlineDashboard() {
  const navigate = useNavigate();
  const { data: patients = [], isLoading } = useQuery({
    queryKey: ['patients', WORKFLOW],
    queryFn: () => getPatients(WORKFLOW),
  });

  // Calculate metrics based on call data
  const today = new Date().toISOString().split('T')[0];
  const metrics = {
    total: patients.length,
    completed: patients.filter(p => p.call_status === 'Completed').length,
    transferred: patients.filter(p => p.routed_to && p.routed_to !== 'Answered Directly').length,
    answeredDirectly: patients.filter(p => p.routed_to === 'Answered Directly' || (!p.routed_to && p.call_status === 'Completed')).length,
    inProgress: patients.filter(p => p.call_status === 'Dialing' || p.call_status === 'In Progress').length,
    failed: patients.filter(p => p.call_status === 'Failed').length,
    today: patients.filter(p => p.created_at?.startsWith(today)).length,
  };

  if (isLoading) {
    return (
      <WorkflowLayout workflowName={WORKFLOW} title="Dashboard">
        <p className="text-muted-foreground">Loading...</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName={WORKFLOW}
      title="Dashboard"
      actions={
        <Button onClick={() => navigate('/workflows/mainline/calls')}>
          View All Calls
        </Button>
      }
    >
      <div className="space-y-6">
        {/* Metrics Grid */}
        <div className="grid gap-4 md:grid-cols-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Total Calls</CardTitle>
              <Phone className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{metrics.total}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">In Progress</CardTitle>
              <Clock className="h-4 w-4 text-yellow-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-yellow-600">{metrics.inProgress}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Answered</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-green-600">{metrics.answeredDirectly}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Transferred</CardTitle>
              <PhoneForwarded className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-blue-600">{metrics.transferred}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Failed</CardTitle>
              <XCircle className="h-4 w-4 text-red-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-red-600">{metrics.failed}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Today</CardTitle>
              <PhoneIncoming className="h-4 w-4 text-purple-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-purple-600">{metrics.today}</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </WorkflowLayout>
  );
}
