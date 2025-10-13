import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import PatientList from './components/patients/patient-list';
import AddPatientForm from './components/AddPatientForm';
import PatientDetail from './components/PatientDetail';

const Navigation = () => {
  const location = useLocation();
  
  const isActive = (path: string) => location.pathname === path;

  return (
    <nav className="bg-white shadow-sm">
      <div className="max-w-7xl mx-auto flex gap-2">
        <Link
          to="/"
          className={`px-6 py-3 inline-block transition-all border-b-[3px] ${
            isActive('/')
              ? 'text-primary border-primary font-semibold'
              : 'text-muted-foreground border-transparent hover:text-foreground'
          }`}
        >
          Patients
        </Link>
        <Link
          to="/add-patient"
          className={`px-6 py-3 inline-block transition-all border-b-[3px] ${
            isActive('/add-patient')
              ? 'text-primary border-primary font-semibold'
              : 'text-muted-foreground border-transparent hover:text-foreground'
          }`}
        >
          Add Patient
        </Link>
      </div>
    </nav>
  );
};

const App = () => {
  return (
    <Router>
      <div className="min-h-screen bg-background">
        {/* Header */}
        <header className="bg-gradient-to-r from-blue-600 to-purple-600 text-white py-6 shadow-lg">
          <div className="text-center">
            <h1 className="text-3xl font-bold">
              Prior Authorization AI Voice Agent
            </h1>
            <p className="mt-2 text-sm opacity-95">
              AI-powered insurance verification calls
            </p>
          </div>
        </header>

        {/* Navigation */}
        <Navigation />

        {/* Main Content */}
        <main>
          <Routes>
            <Route path="/" element={<PatientList />} />
            <Route path="/add-patient" element={<AddPatientForm />} />
            <Route path="/patient/:patientId" element={<PatientDetail />} />
          </Routes>
        </main>

        {/* Footer */}
        <footer className="text-center py-6 text-muted-foreground text-sm mt-10">
          Healthcare AI Agent © 2025
        </footer>
      </div>
    </Router>
  );
};

export default App;