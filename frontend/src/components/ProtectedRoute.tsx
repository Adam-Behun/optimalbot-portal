import { isAuthenticated, removeAuthToken, emitLogoutEvent } from '@/lib/auth';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  if (!isAuthenticated()) {
    // HIPAA Compliance: Clear all auth data before redirect
    removeAuthToken();
    emitLogoutEvent(); // Triggers context cleanup via SessionCleanup

    // Redirect to landing page if not authenticated
    window.location.href = '/';
    return null;
  }

  return <>{children}</>;
}
