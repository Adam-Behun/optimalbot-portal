import { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { getPatients } from '@/api';
import { Patient } from '@/types';
import { Navigation } from './Navigation';
import { getSelectedWorkflow } from '@/lib/auth';
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Phone, Users, CheckCircle, Clock, FileText } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, XAxis, YAxis, LabelList } from 'recharts';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from '@/components/ui/chart';

export function Dashboard() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadPatients = async () => {
      try {
        const workflow = getSelectedWorkflow();
        const data = await getPatients(workflow || undefined);
        setPatients(data);
      } catch (err) {
        console.error('Error loading patients:', err);
      } finally {
        setLoading(false);
      }
    };

    loadPatients();
  }, []);

  const hasCalls = patients.length > 0;

  // Calculate statistics
  const totalCalls = patients.filter(p => p.call_status && p.call_status !== 'Not Started').length;
  const completedCalls = patients.filter(p => p.call_status === 'Completed').length;
  const inProgressCalls = patients.filter(p => p.call_status === 'In Progress').length;
  const totalPatients = patients.length;

  // Prepare insurance chart data - group by insurance company and auth status
  // Uses flat fields (insurance_company_name, prior_auth_status)
  const insuranceChartData = useMemo(() => {
    const byInsurance = patients.reduce((acc, p) => {
      const insurance = p.insurance_company_name || 'Unknown';
      if (!acc[insurance]) {
        acc[insurance] = { total: 0, Pending: 0, Approved: 0, Denied: 0 };
      }
      acc[insurance].total++;
      const status = p.prior_auth_status || 'Pending';
      if (status in acc[insurance]) {
        acc[insurance][status as keyof typeof acc[typeof insurance]]++;
      }
      return acc;
    }, {} as Record<string, { total: number; Pending: number; Approved: number; Denied: number }>);

    return Object.entries(byInsurance)
      .map(([name, data]) => ({
        insurance: name,
        ...data,
      }))
      .sort((a, b) => b.total - a.total);
  }, [patients]);

  const insuranceChartConfig = {
    Approved: {
      label: 'Approved',
      color: '#22c55e',
    },
    Pending: {
      label: 'Pending',
      color: '#eab308',
    },
    Denied: {
      label: 'Denied',
      color: '#ef4444',
    },
  } satisfies ChartConfig;

  if (loading) {
    return (
      <>
        <Navigation />
        <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
          <p className="text-muted-foreground">Loading dashboard...</p>
        </div>
      </>
    );
  }

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-8">
        {!hasCalls ? (
          /* Empty State */
          <Empty className="border rounded-lg p-12">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Phone className="h-12 w-12" />
              </EmptyMedia>
              <EmptyTitle>No Calls Yet</EmptyTitle>
              <EmptyDescription>
                Get started by adding patients to begin making prior authorization calls.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Link to="/add-patient">
                <Button>Add Your First Patient</Button>
              </Link>
            </EmptyContent>
          </Empty>
        ) : (
          /* Dashboard with Charts and Stats */
          <>
            {/* Stats Cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Patients</CardTitle>
                  <Users className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{totalPatients}</div>
                  <p className="text-xs text-muted-foreground">Patients in system</p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Calls</CardTitle>
                  <Phone className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{totalCalls}</div>
                  <p className="text-xs text-muted-foreground">Authorization calls made</p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Completed</CardTitle>
                  <CheckCircle className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{completedCalls}</div>
                  <p className="text-xs text-muted-foreground">
                    {totalCalls > 0 ? `${Math.round((completedCalls / totalCalls) * 100)}%` : '0%'} completion rate
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">In Progress</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{inProgressCalls}</div>
                  <p className="text-xs text-muted-foreground">Currently active</p>
                </CardContent>
              </Card>
            </div>

            {/* Patients by Insurance & Auth Status Chart */}
            <Card>
              <CardHeader>
                <CardTitle>Patients by Insurance & Auth Status</CardTitle>
                <CardDescription>Total patients and authorization status breakdown by insurance company</CardDescription>
              </CardHeader>
              <CardContent>
                <ChartContainer config={insuranceChartConfig} className="min-h-[300px] w-full">
                  <BarChart
                    accessibilityLayer
                    data={insuranceChartData}
                    layout="vertical"
                    margin={{ right: 40 }}
                  >
                    <CartesianGrid horizontal={false} strokeDasharray="3 3" stroke="transparent" />
                    <YAxis
                      dataKey="insurance"
                      type="category"
                      tickLine={false}
                      axisLine={false}
                      width={180}
                      fontSize={14}
                    />
                    <XAxis type="number" hide={true} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <ChartLegend content={<ChartLegendContent className="text-sm" />} />
                    <Bar
                      dataKey="Approved"
                      stackId="a"
                      fill="var(--color-Approved)"
                      radius={[0, 0, 0, 0]}
                    />
                    <Bar
                      dataKey="Pending"
                      stackId="a"
                      fill="var(--color-Pending)"
                      radius={[0, 0, 0, 0]}
                    />
                    <Bar
                      dataKey="Denied"
                      stackId="a"
                      fill="var(--color-Denied)"
                      radius={[4, 4, 4, 4]}
                    >
                      <LabelList
                        dataKey="total"
                        position="right"
                        offset={8}
                        className="fill-foreground"
                        fontSize={12}
                      />
                    </Bar>
                  </BarChart>
                </ChartContainer>
              </CardContent>
            </Card>

            {/* Custom Reports Link */}
            <Card>
              <CardContent className="flex items-center justify-between py-4">
                <div className="flex items-center gap-3">
                  <FileText className="h-5 w-5 text-muted-foreground" />
                  <span className="text-sm">Need custom analytics? Browse our report library.</span>
                </div>
                <Link to="/custom-reports">
                  <Button variant="outline" size="sm">Custom Reports</Button>
                </Link>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </>
  );
}
