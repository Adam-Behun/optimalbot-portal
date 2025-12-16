import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Patient } from '@/types';
import { getPatients } from '@/api';
import { Phone, CheckCircle, XCircle, Clock, PhoneIncoming } from 'lucide-react';
import { WorkflowLayout } from '../shared/WorkflowLayout';

export function LabResultsDashboard() {
  const navigate = useNavigate();
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPatients('lab_results')
      .then(setPatients)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  // Calculate metrics based on call_status
  const today = new Date().toISOString().split('T')[0];
  const metrics = {
    total: patients.length,
    completed: patients.filter(p => p.call_status === 'Completed' || p.call_status === 'Supervisor Dialed').length,
    completedToday: patients.filter(p => (p.call_status === 'Completed' || p.call_status === 'Supervisor Dialed') && p.created_at?.startsWith(today)).length,
    inProgress: patients.filter(p => p.call_status === 'Dialing' || p.call_status === 'In Progress').length,
    failed: patients.filter(p => p.call_status === 'Failed').length
  };

  if (loading) {
    return (
      <WorkflowLayout workflowName="lab_results" title="Dashboard">
        <p className="text-muted-foreground">Loading...</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName="lab_results"
      title="Dashboard"
      actions={
        <Button onClick={() => navigate('/workflows/lab_results/calls')}>
          View All Calls
        </Button>
      }
    >
      <div className="space-y-6">
        {/* Metrics Grid */}
        <div className="grid gap-4 md:grid-cols-5">
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
              <CardTitle className="text-sm font-medium">Completed</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-green-600">{metrics.completed}</p>
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
              <PhoneIncoming className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-blue-600">{metrics.completedToday}</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </WorkflowLayout>
  );
}
