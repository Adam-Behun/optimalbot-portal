import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { isAuthenticated } from '@/lib/auth';
import { useEffect } from 'react';

export function LandingPage() {
  const navigate = useNavigate();

  useEffect(() => {
    if (isAuthenticated()) {
      navigate('/dashboard');
    }
  }, [navigate]);

  return (
    <div className="min-h-screen">
      <header className="border-b bg-background sticky top-0 z-50">
        <div className="max-w-4xl mx-auto px-4">
          <div className="flex items-center justify-end h-16">
            <div className="flex items-center gap-2">
              <Button onClick={() => navigate('/login')}>
                Log In
              </Button>
            </div>
          </div>
        </div>
      </header>
    </div>
  );
}
