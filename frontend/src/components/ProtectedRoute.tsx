import { isAuthenticated } from '@/lib/auth';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  if (!isAuthenticated()) {
    // Redirect to landing page if not authenticated
    window.location.href = 'https://datasova.com';
    return null;
  }

  return <>{children}</>;
}
