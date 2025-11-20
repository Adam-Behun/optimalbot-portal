import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Navigation } from './Navigation';
import { CalendarIcon, Shield, ClipboardCheck, Calendar as CalendarIcon2, HelpCircle } from 'lucide-react';

interface WorkflowItem {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  enabled: boolean;
  link?: string;
}

const workflows: WorkflowItem[] = [
  {
    id: 'prior-auth',
    title: 'Prior Authorization',
    description: 'Secures specific approval for a particular treatment or service',
    icon: <Shield className="h-5 w-5" />,
    enabled: true,
    link: '/patient-list',
  },
  {
    id: 'eligibility',
    title: 'Eligibility Verification',
    description: 'Confirms active coverage and general benefits',
    icon: <ClipboardCheck className="h-5 w-5" />,
    enabled: false,
  },
  {
    id: 'scheduling',
    title: 'Visit Scheduling',
    description: 'Schedules visit for a patient',
    icon: <CalendarIcon2 className="h-5 w-5" />,
    enabled: false,
  },
  {
    id: 'general-questions',
    title: 'General Questions',
    description: 'Answers patient questions and routes call to the right resource if resolution not found',
    icon: <HelpCircle className="h-5 w-5" />,
    enabled: false,
  },
];

export function Workflows() {
  const [showBooking, setShowBooking] = useState(false);
  const [date, setDate] = useState<Date | undefined>(undefined);
  const [selectedTime, setSelectedTime] = useState<string | null>(null);

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

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto py-8 px-4 space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold">Workflows</h1>
          <p className="text-muted-foreground">
            Manage your automated workflows and request access to new capabilities
          </p>
        </div>

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
              className={workflow.enabled ? '' : 'opacity-50'}
            >
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {workflow.icon}
                  {workflow.title}
                </CardTitle>
                <CardDescription>{workflow.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="h-48 flex items-center justify-center border-2 border-dashed rounded-lg">
                  {workflow.enabled ? (
                    <div className="flex flex-col items-center gap-3">
                      <span className="text-sm text-green-600 dark:text-green-400 font-medium">
                        Active
                      </span>
                      <Link to={workflow.link || '#'}>
                        <Button variant="outline" size="sm">
                          View
                        </Button>
                      </Link>
                    </div>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setShowBooking(true)}
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
    </>
  );
}
