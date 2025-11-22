import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import PatientList from './components/patients/patient-list';
import AddPatientForm from './components/AddPatientForm';
import PatientDetail from './components/PatientDetail';
import { Dashboard } from './components/Dashboard';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/theme-provider";
import { LoginForm } from './components/login-form';
import { ForgotPasswordForm } from './components/forgot-password-form';
import { ResetPasswordForm } from './components/reset-password-form';
import { LandingPage } from './components/LandingPage';
import { ProtectedRoute } from './components/ProtectedRoute';
import { CustomReports } from './components/CustomReports';
import { Workflows } from './components/Workflows';
import { SessionTimeoutModal } from './components/SessionTimeoutModal';

const App = () => {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="healthcare-ui-theme">
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
              <Route path="/dashboard" element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              } />
              <Route path="/patient-list" element={
                <ProtectedRoute>
                  <PatientList />
                </ProtectedRoute>
              } />
              <Route path="/add-patient" element={
                <ProtectedRoute>
                  <AddPatientForm />
                </ProtectedRoute>
              } />
              <Route path="/patient/:patientId" element={
                <ProtectedRoute>
                  <PatientDetail />
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
              </Routes>
            </main>
          </div>
          <Toaster />
        </SessionTimeoutModal>
      </Router>
    </ThemeProvider>
  );
};

export default App;