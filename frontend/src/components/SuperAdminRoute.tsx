import { Navigate } from 'react-router-dom';
import { isAuthenticated, getAuthUser, removeAuthToken, emitLogoutEvent } from '@/lib/auth';

interface SuperAdminRouteProps {
  children: React.ReactNode;
}

export function SuperAdminRoute({ children }: SuperAdminRouteProps) {
  const user = getAuthUser();

  if (!isAuthenticated()) {
    // Clear all auth data before redirect
    removeAuthToken();
    emitLogoutEvent();
    return <Navigate to="/" replace />;
  }

  if (!user?.is_super_admin) {
    // Redirect non-super-admin users to home
    return <Navigate to="/home" replace />;
  }

  return <>{children}</>;
}
