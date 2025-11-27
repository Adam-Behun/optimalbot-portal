import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LoginForm } from './components/LoginForm';
import { ForgotPasswordForm } from './components/ForgotPasswordForm';
import { ResetPasswordForm } from './components/ResetPasswordForm';
import { LandingPage } from './components/LandingPage';
import { ProtectedRoute } from './components/ProtectedRoute';
import { CustomReports } from './components/CustomReports';
import { SessionTimeoutModal } from './components/SessionTimeoutModal';
import { Home } from './components/Home';
import { OrganizationProvider } from './contexts/OrganizationContext';
import { SidebarLayout } from './components/SidebarLayout';
import {
  PriorAuthDashboard,
  PriorAuthPatientList,
  PriorAuthAddPatient,
} from './components/workflows/prior_auth';
import {
  PatientQuestionsDashboard,
  PatientQuestionsCallList,
} from './components/workflows/patient_questions';
import {
  PatientIntakeDashboard,
  PatientIntakeCallList,
} from './components/workflows/patient_intake';

const App = () => {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="healthcare-ui-theme">
      <OrganizationProvider>
        <Router>
          <SessionTimeoutModal>
            <div className="min-h-screen bg-background">
              <main>
                <Routes>
              {/* Public routes */}
              <Route path="/" element={<LandingPage />} />
              <Route path="/login" element={
                <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
                  <div className="w-full max-w-sm">
                    <LoginForm />
                  </div>
                </div>
              } />
              <Route path="/forgot-password" element={
                <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
                  <div className="w-full max-w-sm">
                    <ForgotPasswordForm />
                  </div>
                </div>
              } />
              <Route path="/reset-password" element={
                <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
                  <div className="w-full max-w-sm">
                    <ResetPasswordForm />
                  </div>
                </div>
              } />

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

              {/* Prior Auth Workflow Routes */}
              <Route path="/workflows/prior_auth/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PriorAuthDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PriorAuthPatientList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients/add" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PriorAuthAddPatient />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Patient Questions Workflow Routes */}
              <Route path="/workflows/patient_questions/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientQuestionsDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/patient_questions/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientQuestionsCallList />
                  </SidebarLayout>
                </ProtectedRoute>
              } />

              {/* Patient Intake Workflow Routes */}
              <Route path="/workflows/patient_intake/dashboard" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientIntakeDashboard />
                  </SidebarLayout>
                </ProtectedRoute>
              } />
              <Route path="/workflows/patient_intake/calls" element={
                <ProtectedRoute>
                  <SidebarLayout>
                    <PatientIntakeCallList />
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