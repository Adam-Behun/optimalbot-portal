import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Patient } from '@/types';
import { getPatients } from '@/api';
import { Users, CheckCircle, XCircle, Clock, Phone } from 'lucide-react';
import { WorkflowLayout } from '../shared/WorkflowLayout';

export function PriorAuthDashboard() {
  const navigate = useNavigate();
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getPatients('prior_auth')
      .then(setPatients)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  // Prior auth specific metrics
  const metrics = {
    total: patients.length,
    pending: patients.filter(p => p.prior_auth_status === 'Pending').length,
    approved: patients.filter(p => p.prior_auth_status === 'Approved').length,
    denied: patients.filter(p => p.prior_auth_status === 'Denied').length,
    completedCalls: patients.filter(p => p.call_status === 'Completed').length
  };

  const recentPatients = [...patients]
    .sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime())
    .slice(0, 5);

  if (loading) {
    return (
      <WorkflowLayout workflowName="prior_auth" title="Dashboard">
        <p className="text-muted-foreground">Loading...</p>
      </WorkflowLayout>
    );
  }

  return (
    <WorkflowLayout
      workflowName="prior_auth"
      title="Dashboard"
      actions={
        <Button onClick={() => navigate('/workflows/prior_auth/patients')}>
          View All Patients
        </Button>
      }
    >
      <div className="space-y-6">
        {/* Metrics Grid */}
        <div className="grid gap-4 md:grid-cols-5">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Total Patients</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{metrics.total}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Pending</CardTitle>
              <Clock className="h-4 w-4 text-yellow-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-yellow-600">{metrics.pending}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Approved</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-green-600">{metrics.approved}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Denied</CardTitle>
              <XCircle className="h-4 w-4 text-red-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-red-600">{metrics.denied}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
              <CardTitle className="text-sm font-medium">Calls Completed</CardTitle>
              <Phone className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold text-blue-600">{metrics.completedCalls}</p>
            </CardContent>
          </Card>
        </div>

        {/* Recent Activity */}
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
          </CardHeader>
          <CardContent>
            {recentPatients.length === 0 ? (
              <p className="text-muted-foreground">No patients yet</p>
            ) : (
              <div className="space-y-3">
                {recentPatients.map(patient => (
                  <div
                    key={patient.patient_id}
                    className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/50 cursor-pointer"
                    onClick={() => navigate(`/workflows/prior_auth/patients/${patient.patient_id}`)}
                  >
                    <div>
                      <p className="font-medium">{patient.patient_name}</p>
                      <p className="text-sm text-muted-foreground">
                        Updated {patient.updated_at ? new Date(patient.updated_at).toLocaleDateString() : 'N/A'}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{patient.call_status}</Badge>
                      {patient.prior_auth_status && (
                        <Badge
                          variant={patient.prior_auth_status === 'Denied' ? 'destructive' : 'secondary'}
                          className={patient.prior_auth_status === 'Approved' ? 'bg-green-100 text-green-800' : ''}
                        >
                          {patient.prior_auth_status}
                        </Badge>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Quick Actions */}
        <div className="flex gap-4">
          <Button onClick={() => navigate('/workflows/prior_auth/patients/add')}>
            Add New Patient
          </Button>
          <Button variant="outline" onClick={() => navigate('/workflows/prior_auth/patients')}>
            View All Patients
          </Button>
        </div>
      </div>
    </WorkflowLayout>
  );
}
