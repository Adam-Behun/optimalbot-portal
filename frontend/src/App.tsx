import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LoginRedirect, ForgotPasswordRedirect, ResetPasswordRedirect } from './components/LoginRedirect';
import { AuthCallback } from './components/AuthCallback';
import { ProtectedRoute } from './components/ProtectedRoute';
import { CustomReports } from './components/CustomReports';
import { SessionTimeoutModal } from './components/SessionTimeoutModal';
import { Home } from './components/Home';
import { OrganizationProvider } from './contexts/OrganizationContext';
import { SidebarLayout } from './components/SidebarLayout';
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

const App = () => {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="healthcare-ui-theme">
      <OrganizationProvider>
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
  );
};

export default App;