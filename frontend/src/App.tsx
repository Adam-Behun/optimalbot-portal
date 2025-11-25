import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/ThemeProvider";
import { LoginForm } from './components/LoginForm';
import { ForgotPasswordForm } from './components/ForgotPasswordForm';
import { ResetPasswordForm } from './components/ResetPasswordForm';
import { LandingPage } from './components/LandingPage';
import { ProtectedRoute } from './components/ProtectedRoute';
import { CustomReports } from './components/CustomReports';
import { Workflows } from './components/Workflows';
import { SessionTimeoutModal } from './components/SessionTimeoutModal';
import { Home } from './components/Home';
import { OrganizationProvider } from './contexts/OrganizationContext';
import {
  PriorAuthDashboard,
  PriorAuthPatientList,
  PriorAuthAddPatient,
  PriorAuthEditPatient,
  PriorAuthPatientDetail,
} from './components/workflows/prior_auth';
import {
  PatientQuestionsDashboard,
  PatientQuestionsCallList,
  PatientQuestionsCallDetail,
} from './components/workflows/patient_questions';

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
                  <Home />
                </ProtectedRoute>
              } />
              <Route path="/custom-reports" element={
                <ProtectedRoute>
                  <CustomReports />
                </ProtectedRoute>
              } />
              <Route path="/workflows" element={
                <ProtectedRoute>
                  <Workflows />
                </ProtectedRoute>
              } />

              {/* Prior Auth Workflow Routes */}
              <Route path="/workflows/prior_auth/dashboard" element={
                <ProtectedRoute>
                  <PriorAuthDashboard />
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients" element={
                <ProtectedRoute>
                  <PriorAuthPatientList />
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients/add" element={
                <ProtectedRoute>
                  <PriorAuthAddPatient />
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients/:id" element={
                <ProtectedRoute>
                  <PriorAuthPatientDetail />
                </ProtectedRoute>
              } />
              <Route path="/workflows/prior_auth/patients/:id/edit" element={
                <ProtectedRoute>
                  <PriorAuthEditPatient />
                </ProtectedRoute>
              } />

              {/* Patient Questions Workflow Routes */}
              <Route path="/workflows/patient_questions/dashboard" element={
                <ProtectedRoute>
                  <PatientQuestionsDashboard />
                </ProtectedRoute>
              } />
              <Route path="/workflows/patient_questions/calls" element={
                <ProtectedRoute>
                  <PatientQuestionsCallList />
                </ProtectedRoute>
              } />
              <Route path="/workflows/patient_questions/calls/:id" element={
                <ProtectedRoute>
                  <PatientQuestionsCallDetail />
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