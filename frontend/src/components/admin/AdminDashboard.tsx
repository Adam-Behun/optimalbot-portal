import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
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
import { getAdminDashboard, AdminDashboard as AdminDashboardType } from '@/api';
import { Phone, CheckCircle, DollarSign, AlertCircle, RefreshCw } from 'lucide-react';
import { formatDatetime } from '@/lib/utils';

export function AdminDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<AdminDashboardType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = () => {
    setLoading(true);
    setError(null);
    getAdminDashboard()
      .then(setData)
      .catch((err) => setError(err.message || 'Failed to load dashboard'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Admin Dashboard</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-6">Admin Dashboard</h1>
        <div className="flex flex-col items-center justify-center py-8 gap-4">
          <p className="text-destructive">{error}</p>
          <Button onClick={fetchData} variant="outline">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Admin Dashboard</h1>
        <Button onClick={fetchData} variant="outline" size="sm">
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Metrics Grid */}
      <div className="grid gap-4 md:grid-cols-3 mb-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Calls Today</CardTitle>
            <Phone className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{data?.calls_today ?? 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Success Rate</CardTitle>
            <CheckCircle className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-green-600">{data?.success_rate ?? 0}%</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Cost Today</CardTitle>
            <DollarSign className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-blue-600">${data?.cost_today_usd?.toFixed(4) ?? '0.0000'}</p>
          </CardContent>
        </Card>
      </div>

      {/* Quick Links */}
      <div className="flex gap-2 mb-6">
        <Button onClick={() => navigate('/admin/calls')}>View All Calls</Button>
        <Button variant="outline" onClick={() => navigate('/admin/costs')}>Cost Report</Button>
        <Button variant="outline" onClick={() => navigate('/admin/onboarding')}>Onboarding</Button>
      </div>

      {/* Recent Failures */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 text-red-500" />
            Recent Failures
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data?.recent_failures && data.recent_failures.length > 0 ? (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Session ID</TableHead>
                    <TableHead>Organization</TableHead>
                    <TableHead>Workflow</TableHead>
                    <TableHead>Date</TableHead>
                    <TableHead>Error</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.recent_failures.map((failure) => (
                    <TableRow
                      key={failure.session_id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => navigate(`/admin/calls/${failure.session_id}`)}
                    >
                      <TableCell className="font-mono text-sm">
                        {failure.session_id?.substring(0, 12)}...
                      </TableCell>
                      <TableCell>{failure.organization_name}</TableCell>
                      <TableCell>{failure.workflow}</TableCell>
                      <TableCell>{formatDatetime(failure.created_at)}</TableCell>
                      <TableCell className="text-red-600">{failure.error}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <p className="text-muted-foreground text-center py-4">No recent failures</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
