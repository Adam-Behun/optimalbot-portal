import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Navigation } from './Navigation';
import { CalendarIcon, BarChart3, PieChart, TrendingUp } from 'lucide-react';

export function CustomReports() {
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
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Custom Reports</h1>
            <p className="text-muted-foreground">
              Browse our report library or request a custom report from our team
            </p>
          </div>
          <Button onClick={() => setShowBooking(true)}>
            <CalendarIcon className="mr-2 h-4 w-4" />
            Request a Report
          </Button>
        </div>

        {/* Booking Calendar Modal/Section */}
        {showBooking && (
          <Card className="gap-0 p-0">
            <CardHeader>
              <CardTitle>Schedule a Report Request</CardTitle>
              <CardDescription>
                Book a 30-minute call to discuss your custom report needs
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col md:flex-row p-0">
              <div className="flex-1 p-6 flex items-center justify-center">
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
                  className="bg-transparent p-0 [--cell-size:2.5rem] md:[--cell-size:3rem]"
                  formatters={{
                    formatWeekdayName: (date) => {
                      return date.toLocaleString('en-US', { weekday: 'short' });
                    },
                  }}
                />
              </div>
              <div className="no-scrollbar flex max-h-72 w-full scroll-pb-6 flex-col gap-4 overflow-y-auto border-t p-6 md:max-h-96 md:w-48 md:border-t-0 md:border-l">
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
            <CardFooter className="flex flex-col gap-4 border-t px-6 !py-5 md:flex-row">
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
                  variant="outline"
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
        )}

        {/* Empty Chart Placeholders */}
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                Authorization Trends
              </CardTitle>
              <CardDescription>Monthly authorization volume</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-48 flex items-center justify-center border-2 border-dashed rounded-lg">
                <span className="text-muted-foreground text-sm">Chart coming soon</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <PieChart className="h-4 w-4" />
                Approval Rates
              </CardTitle>
              <CardDescription>By insurance provider</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-48 flex items-center justify-center border-2 border-dashed rounded-lg">
                <span className="text-muted-foreground text-sm">Chart coming soon</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="h-4 w-4" />
                Call Performance
              </CardTitle>
              <CardDescription>Average call duration and success rate</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-48 flex items-center justify-center border-2 border-dashed rounded-lg">
                <span className="text-muted-foreground text-sm">Chart coming soon</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                CPT Code Analysis
              </CardTitle>
              <CardDescription>Top procedure codes by volume</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="h-48 flex items-center justify-center border-2 border-dashed rounded-lg">
                <span className="text-muted-foreground text-sm">Chart coming soon</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
