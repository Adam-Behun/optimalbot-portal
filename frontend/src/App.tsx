import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import PatientList from './components/PatientList';
import AddPatientForm from './components/AddPatientForm';
import PatientDetail from './components/PatientDetail';

const Navigation: React.FC = () => {
  const location = useLocation();
  
  const isActive = (path: string) => {
    return location.pathname === path;
  };

  const tabStyle = (path: string): React.CSSProperties => ({
    padding: '12px 24px',
    textDecoration: 'none',
    color: isActive(path) ? '#667eea' : '#666',
    borderBottom: isActive(path) ? '3px solid #667eea' : '3px solid transparent',
    fontWeight: isActive(path) ? 600 : 400,
    transition: 'all 0.2s',
    display: 'inline-block'
  });

  return (
    <nav style={{
      backgroundColor: 'white',
      boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
      marginBottom: '0'
    }}>
      <div style={{
        maxWidth: '1200px',
        margin: '0 auto',
        display: 'flex',
        gap: '8px'
      }}>
        <Link to="/" style={tabStyle('/')}>
          Patients
        </Link>
        <Link to="/add-patient" style={tabStyle('/add-patient')}>
          Add Patient
        </Link>
      </div>
    </nav>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <div style={{
        minHeight: '100vh',
        backgroundColor: '#f5f7fa'
      }}>
        {/* Header */}
        <header style={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          color: 'white',
          padding: '24px 20px',
          textAlign: 'center',
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
        }}>
          <h1 style={{ margin: 0, fontSize: '32px' }}>
            Prior Authorization AI Voice Agent
          </h1>
          <p style={{ margin: '8px 0 0 0', fontSize: '16px', opacity: 0.95 }}>
            AI-powered insurance verification calls
          </p>
        </header>

        {/* Navigation */}
        <Navigation />

        {/* Main Content */}
        <main style={{
          maxWidth: '1200px',
          margin: '0 auto',
          padding: '20px'
        }}>
          <Routes>
            <Route path="/" element={<PatientList />} />
            <Route path="/add-patient" element={<AddPatientForm />} />
            <Route path="/patient/:patientId" element={<PatientDetail />} />
          </Routes>
        </main>

        {/* Footer */}
        <footer style={{
          textAlign: 'center',
          padding: '20px',
          color: '#666',
          fontSize: '14px',
          marginTop: '40px'
        }}>
          Healthcare AI Agent © 2025
        </footer>
      </div>
    </Router>
  );
};

export default App;