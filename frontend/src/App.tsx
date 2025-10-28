import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import PatientList from './components/patients/patient-list';
import AddPatientForm from './components/AddPatientForm';
import PatientDetail from './components/PatientDetail';
import { Toaster } from "@/components/ui/sonner";
import { ThemeProvider } from "@/components/theme-provider";
import { SignupForm } from './components/signup-form';
import { LoginForm } from './components/login-form';

const App = () => {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="healthcare-ui-theme">
      <Router>
        <div className="min-h-screen bg-background">
          <main>
            <Routes>
              <Route path="/signup" element={
                <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
                  <div className="w-full max-w-sm">
                    <SignupForm />
                  </div>
                </div>
              } />
              <Route path="/login" element={
                <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
                  <div className="w-full max-w-sm">
                    <LoginForm />
                  </div>
                </div>
              } />
              <Route path="/" element={<PatientList />} />
              <Route path="/add-patient" element={<AddPatientForm />} />
              <Route path="/patient/:patientId" element={<PatientDetail />} />
            </Routes>
          </main>
        </div>
        <Toaster />
      </Router>
    </ThemeProvider>
  );
};

export default App;