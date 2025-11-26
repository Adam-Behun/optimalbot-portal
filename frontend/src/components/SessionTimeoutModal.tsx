import { useState, useEffect, useCallback, useRef } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { removeAuthToken, isAuthenticated } from '@/lib/auth';

// Session timeout configuration (in milliseconds)
const INACTIVITY_TIMEOUT = 30 * 60 * 1000; // 30 minutes of inactivity
const WARNING_BEFORE_TIMEOUT = 10 * 1000; // Show warning 10 seconds before logout

interface SessionTimeoutModalProps {
  children: React.ReactNode;
}

export function SessionTimeoutModal({ children }: SessionTimeoutModalProps) {
  const [showWarning, setShowWarning] = useState(false);
  const [countdown, setCountdown] = useState(10);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  const countdownRef = useRef<NodeJS.Timeout | null>(null);
  const lastActivityRef = useRef<number>(Date.now());

  const handleLogout = useCallback(() => {
    removeAuthToken();
    window.location.href = '/';
  }, []);

  const resetTimer = useCallback(() => {
    if (!isAuthenticated()) return;

    lastActivityRef.current = Date.now();

    // Clear existing timers
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
    }

    // Hide warning if shown
    setShowWarning(false);
    setCountdown(10);

    // Set new timeout for warning
    timeoutRef.current = setTimeout(() => {
      setShowWarning(true);
      setCountdown(10);

      // Start countdown
      countdownRef.current = setInterval(() => {
        setCountdown((prev) => {
          if (prev <= 1) {
            if (countdownRef.current) {
              clearInterval(countdownRef.current);
            }
            handleLogout();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }, INACTIVITY_TIMEOUT - WARNING_BEFORE_TIMEOUT);
  }, [handleLogout]);

  const handleContinueWorking = useCallback(() => {
    resetTimer();
  }, [resetTimer]);

  // Track user activity
  useEffect(() => {
    if (!isAuthenticated()) return;

    const activityEvents = [
      'mousedown',
      'mousemove',
      'keydown',
      'scroll',
      'touchstart',
      'click',
    ];

    const handleActivity = () => {
      // Only reset if warning is not shown (to prevent accidental dismissal)
      if (!showWarning) {
        resetTimer();
      }
    };

    // Add event listeners
    activityEvents.forEach((event) => {
      document.addEventListener(event, handleActivity);
    });

    // Initial timer setup
    resetTimer();

    // Cleanup
    return () => {
      activityEvents.forEach((event) => {
        document.removeEventListener(event, handleActivity);
      });
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      if (countdownRef.current) {
        clearInterval(countdownRef.current);
      }
    };
  }, [resetTimer, showWarning]);

  // Don't render modal if not authenticated
  if (!isAuthenticated()) {
    return <>{children}</>;
  }

  return (
    <>
      {children}
      <AlertDialog open={showWarning} onOpenChange={setShowWarning}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Session Timeout Warning</AlertDialogTitle>
            <AlertDialogDescription className="space-y-3">
              <p>
                Your session is about to expire due to inactivity. For security
                purposes, you will be automatically logged out.
              </p>
              <p className="text-center text-3xl font-bold text-destructive">
                {countdown}
              </p>
              <p className="text-center text-sm">
                seconds remaining
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" onClick={handleLogout}>
              Log Out
            </Button>
            <Button onClick={handleContinueWorking}>
              Continue Working
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
