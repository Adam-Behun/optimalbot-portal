import { useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LoginRedirect, ForgotPasswordRedirect, ResetPasswordRedirect } from './components/LoginRedirect';
import { AuthCallback } from './components/AuthCallback';
import { Status } from './components/Status';
import { Settings } from './components/Settings';
import { ProtectedRoute } from './components/ProtectedRoute';
import { CustomReports } from './components/CustomReports';
import { SessionTimeoutModal } from './components/SessionTimeoutModal';
import { Home } from './components/Home';
import { OrganizationProvider, useOrganization } from './contexts/OrganizationContext';
import { SidebarLayout } from './components/SidebarLayout';
import { removeAuthToken, onLogoutEvent } from './lib/auth';
import {
  EligibilityVerificationDashboard,
  EligibilityVerificationPatientList,
  EligibilityVerificationAddPatient,
} from './components/workflows/eligibility_verification';
import {
  PatientSchedulingDashboard,
  PatientSchedulingCallList,
} from './components/workflows/patient_scheduling';
import {
  MainlineDashboard,
  MainlineCallList,
} from './components/workflows/mainline';
import {
  LabResultsDashboard,
  LabResultsCallList,
} from './components/workflows/lab_results';
import {
  PrescriptionStatusDashboard,
  PrescriptionStatusCallList,
} from './components/workflows/prescription_status';

// HIPAA Compliance: Clear session data on tab close and handle logout events
function SessionCleanup() {
  const { clearOrganization } = useOrganization();

  useEffect(() => {
    // Clear session data when tab/browser is closed
    const handleBeforeUnload = () => {
      removeAuthToken();
    };

    // Listen for logout events from API interceptors
    const unsubscribe = onLogoutEvent(() => {
      clearOrganization();
    });

    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      unsubscribe();
    };
  }, [clearOrganization]);

  return null;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 30,        // Data fresh for 30s
      refetchOnWindowFocus: false, // Disable for healthcare (avoid surprise refreshes)
      retry: 1,
    },
  },
});

const App = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider defaultTheme="dark" storageKey="healthcare-ui-theme">
        <OrganizationProvider>
        <SessionCleanup />
        <Router>
          <SessionTimeoutModal>
            <div className="min-h-screen bg-background">
              <main>
                <Routes>
              {/* Public routes - redirect root to login */}
              <Route path="/" element={<Navigate to="/login" replace />} />
              <Route path="/login" element={<LoginRedirect />} />
              <Route path="/forgot-password" element={<ForgotPasswordRedirect />} />
              <Route path="/reset-password" element={<ResetPasswordRedirect />} />
              <Route path="/auth/callback" element={<AuthCallback />} />
              <Route path="/status" element={<Status />} />

              {/* Protected routes - require authentication */}
              <Route path="/home" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <Home />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/custom-reports" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <CustomReports />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/settings" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <Settings />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Eligibility Verification Workflow Routes */}
              <Route path="/workflows/eligibility_verification/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <EligibilityVerificationDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/eligibility_verification/patients" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <EligibilityVerificationPatientList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/eligibility_verification/patients/add" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <EligibilityVerificationAddPatient />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Patient Scheduling Workflow Routes */}
              <Route path="/workflows/patient_scheduling/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientSchedulingDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/patient_scheduling/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientSchedulingCallList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Main Line Workflow Routes */}
              <Route path="/workflows/mainline/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <MainlineDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/mainline/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <MainlineCallList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Lab Results Workflow Routes */}
              <Route path="/workflows/lab_results/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <LabResultsDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/lab_results/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <LabResultsCallList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Prescription Status Workflow Routes */}
              <Route path="/workflows/prescription_status/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PrescriptionStatusDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/prescription_status/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PrescriptionStatusCallList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              </Routes>
              </main>
            </div>
            <Toaster />
          </SessionTimeoutModal>
        </Router>
        </OrganizationProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

export default App;