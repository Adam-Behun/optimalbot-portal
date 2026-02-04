import { useState, useEffect } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CalendarIcon, Shield, Phone, Calendar as CalendarIcon2, HelpCircle, Microscope, Pill, PhoneCall, CheckCircle, XCircle, Voicemail, Clock, DollarSign, TrendingUp } from 'lucide-react';
import { getOrganization, setSelectedWorkflow, getAuthUser } from '@/lib/auth';
import { getMetricsSummary, MetricsSummary } from '@/api';

const workflowIcons: Record<string, React.ReactNode> = {
  'eligibility_verification': <Shield className="h-5 w-5" />,
  'patient_scheduling': <CalendarIcon2 className="h-5 w-5" />,
  'mainline': <Phone className="h-5 w-5" />,
  'lab_results': <Microscope className="h-5 w-5" />,
  'prescription_status': <Pill className="h-5 w-5" />,
};

const workflowDescriptions: Record<string, string> = {
  'eligibility_verification': 'Verifies patient eligibility and benefits with insurance companies',
  'patient_scheduling': 'Inbound calls for appointment scheduling',
  'mainline': 'Main phone line - answer questions or route to departments',
  'lab_results': 'Inbound calls for lab result inquiries',
  'prescription_status': 'Inbound calls for prescription refill inquiries',
};

export function Home() {
  const navigate = useNavigate();
  const [showBooking, setShowBooking] = useState(false);
  const [date, setDate] = useState<Date | undefined>(undefined);
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [metricsPeriod, setMetricsPeriod] = useState<'day' | 'week' | 'month'>('day');

  // Get workflows from organization
  const org = getOrganization();
  const user = getAuthUser();

  // Redirect super admins without organization to /admin
  if (user?.is_super_admin && !org) {
    return <Navigate to="/admin" replace />;
  }

  // Fetch metrics
  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        setMetricsLoading(true);
        const data = await getMetricsSummary(metricsPeriod);
        setMetrics(data);
      } catch (err) {
        console.error('Failed to fetch metrics:', err);
      } finally {
        setMetricsLoading(false);
      }
    };
    fetchMetrics();
  }, [metricsPeriod]);
  const orgWorkflows = org?.workflows || {};

  // Convert org workflows to display format
  const workflows = Object.entries(orgWorkflows).map(([id, config]) => ({
    id,
    title: id.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '),
    description: workflowDescriptions[id] || 'Workflow for ' + id,
    icon: workflowIcons[id] || <HelpCircle className="h-5 w-5" />,
    enabled: config.enabled,
    fieldCount: config.patient_schema?.fields?.length || 0,
  }));

  const handleSelectWorkflow = (workflowId: string) => {
    setSelectedWorkflow(workflowId);
    navigate(`/workflows/${workflowId}/dashboard`);
  };

  // Generate 30-minute time slots from 9 AM to 5 PM
  const timeSlots = Array.from({ length: 17 }, (_, i) => {
    const totalMinutes = i * 30;
    const hour = Math.floor(totalMinutes / 60) + 9;
    const minute = totalMinutes % 60;
    return `${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`;
  });

  // Example booked dates (unavailable)
  const bookedDates = [
    new Date(2025, 10, 21),
    new Date(2025, 10, 22),
    new Date(2025, 10, 28),
  ];

  // Disable weekends and past dates
  const disabledDays = (date: Date) => {
    const day = date.getDay();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return day === 0 || day === 6 || date < today || bookedDates.some(
      d => d.toDateString() === date.toDateString()
    );
  };

  const handleBookMeeting = () => {
    if (date && selectedTime) {
      alert(`Meeting booked for ${date.toLocaleDateString('en-US', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
      })} at ${selectedTime}`);
      setShowBooking(false);
      setDate(undefined);
      setSelectedTime(null);
    }
  };

  const formatDuration = (seconds: number) => {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${minutes}m ${secs}s`;
  };

  const periodLabels: Record<string, string> = {
    day: 'Today',
    week: 'This Week',
    month: 'This Month',
  };

  return (
    <div className="w-full space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">{org?.name || 'Welcome'}</h1>
            <p className="text-muted-foreground">
              Choose a workflow to manage patients and calls
            </p>
          </div>
          <Button onClick={() => setShowBooking(true)}>
            <CalendarIcon className="mr-2 h-4 w-4" />
            Request a Workflow
          </Button>
        </div>

        {/* Dashboard Metrics */}
        {!showBooking && (
          <div className="space-y-4">
            {/* Period Selector */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Show:</span>
              {(['day', 'week', 'month'] as const).map((period) => (
                <Badge
                  key={period}
                  variant={metricsPeriod === period ? 'default' : 'outline'}
                  className="cursor-pointer"
                  onClick={() => setMetricsPeriod(period)}
                >
                  {periodLabels[period]}
                </Badge>
              ))}
            </div>

            {/* Metrics Cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              {/* Total Calls */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Calls</CardTitle>
                  <PhoneCall className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {metricsLoading ? (
                    <div className="h-8 w-16 animate-pulse bg-muted rounded" />
                  ) : (
                    <div className="text-2xl font-bold">{metrics?.total_calls ?? 0}</div>
                  )}
                  <p className="text-xs text-muted-foreground">{periodLabels[metricsPeriod]}</p>
                </CardContent>
              </Card>

              {/* Success Rate */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Success Rate</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {metricsLoading ? (
                    <div className="h-8 w-16 animate-pulse bg-muted rounded" />
                  ) : (
                    <div className="text-2xl font-bold">
                      {metrics?.success_rate !== undefined ? `${Math.round(metrics.success_rate)}%` : '—'}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {metrics ? `${metrics.completed} completed` : ''}
                  </p>
                </CardContent>
              </Card>

              {/* Avg Duration */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Avg Duration</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {metricsLoading ? (
                    <div className="h-8 w-16 animate-pulse bg-muted rounded" />
                  ) : (
                    <div className="text-2xl font-bold">
                      {metrics?.avg_duration_seconds ? formatDuration(metrics.avg_duration_seconds) : '—'}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">Per call</p>
                </CardContent>
              </Card>

              {/* Cost */}
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Cost</CardTitle>
                  <DollarSign className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  {metricsLoading ? (
                    <div className="h-8 w-16 animate-pulse bg-muted rounded" />
                  ) : (
                    <div className="text-2xl font-bold">
                      ${metrics?.total_cost_usd?.toFixed(2) ?? '0.00'}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">{periodLabels[metricsPeriod]}</p>
                </CardContent>
              </Card>
            </div>

            {/* Call Status Breakdown */}
            {metrics && (metrics.completed > 0 || metrics.failed > 0 || metrics.voicemail > 0) && (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">Call Status Breakdown</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex gap-6 flex-wrap">
                    <div className="flex items-center gap-2">
                      <CheckCircle className="h-4 w-4 text-green-500" />
                      <span className="text-sm">Completed: {metrics.completed}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <XCircle className="h-4 w-4 text-red-500" />
                      <span className="text-sm">Failed: {metrics.failed}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Voicemail className="h-4 w-4 text-amber-500" />
                      <span className="text-sm">Voicemail: {metrics.voicemail}</span>
                    </div>
                    {metrics.in_progress > 0 && (
                      <div className="flex items-center gap-2">
                        <Phone className="h-4 w-4 text-blue-500 animate-pulse" />
                        <span className="text-sm">In Progress: {metrics.in_progress}</span>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        )}

        {/* Booking Calendar Modal/Section */}
        {showBooking && (
          <div className="flex justify-center">
            <Card className="gap-0 p-0 w-fit">
              <CardHeader className="p-4">
                <CardTitle className="text-lg">Request Workflow Access</CardTitle>
                <CardDescription>
                  Book a 30-minute call to discuss enabling this workflow for your account
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col md:flex-row p-0 md:justify-center">
                <div className="p-4">
                <Calendar
                  mode="single"
                  selected={date}
                  onSelect={setDate}
                  defaultMonth={date}
                  disabled={disabledDays}
                  showOutsideDays={false}
                  modifiers={{
                    booked: bookedDates,
                  }}
                  modifiersClassNames={{
                    booked: '[&>button]:line-through opacity-100',
                  }}
                  className="bg-transparent p-0 [--cell-size:2rem]"
                  formatters={{
                    formatWeekdayName: (date) => {
                      return date.toLocaleString('en-US', { weekday: 'short' });
                    },
                  }}
                />
              </div>
              <div className="no-scrollbar flex max-h-56 w-full scroll-pb-4 flex-col gap-3 overflow-y-auto border-t p-4 md:max-h-72 md:w-40 md:border-t-0 md:border-l">
                <div className="grid gap-2">
                  {timeSlots.map((time) => (
                    <Button
                      key={time}
                      variant={selectedTime === time ? 'default' : 'outline'}
                      onClick={() => setSelectedTime(time)}
                      className="w-full shadow-none"
                    >
                      {time}
                    </Button>
                  ))}
                </div>
              </div>
            </CardContent>
              <CardFooter className="flex flex-col gap-3 border-t px-4 py-4 md:flex-row">
                <div className="text-sm">
                  {date && selectedTime ? (
                    <>
                      Your meeting is booked for{' '}
                      <span className="font-medium">
                        {' '}
                        {date?.toLocaleDateString('en-US', {
                          weekday: 'long',
                          day: 'numeric',
                          month: 'long',
                        })}{' '}
                      </span>
                      at <span className="font-medium">{selectedTime}</span>.
                    </>
                  ) : (
                    <>Select a date and time for your meeting.</>
                  )}
                </div>
                <div className="flex gap-2 w-full md:ml-auto md:w-auto">
                  <Button
                    variant="destructive"
                    onClick={() => setShowBooking(false)}
                    className="flex-1 md:flex-none"
                  >
                    Cancel
                  </Button>
                  <Button
                    disabled={!date || !selectedTime}
                    onClick={handleBookMeeting}
                    className="flex-1 md:flex-none"
                  >
                    Confirm Booking
                  </Button>
                </div>
              </CardFooter>
            </Card>
          </div>
        )}

        {/* Workflow Cards */}
        {!showBooking && <div className="grid gap-4 md:grid-cols-2">
          {workflows.map((workflow) => (
            <Card
              key={workflow.id}
              className={workflow.enabled ? 'cursor-pointer hover:border-primary transition-colors' : 'opacity-50'}
              onClick={() => workflow.enabled && handleSelectWorkflow(workflow.id)}
            >
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {workflow.icon}
                  {workflow.title}
                </CardTitle>
                <CardDescription>{workflow.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-32 flex items-center justify-center border-2 border-dashed rounded-lg">
                  {workflow.enabled ? (
                    <div className="flex flex-col items-center gap-2">
                      <span className="text-sm text-green-600 dark:text-green-400 font-medium">
                        Active
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {workflow.fieldCount} fields
                      </span>
                      <Button variant="outline" size="sm">
                        Select
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowBooking(true);
                      }}
                    >
                      <CalendarIcon className="mr-2 h-4 w-4" />
                      Request Access
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>}
    </div>
  );
}
