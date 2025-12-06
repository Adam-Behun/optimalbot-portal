import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { exchangeToken } from '../api';
import { setAuthToken, setAuthUser } from '../lib/auth';
import { useOrganization } from '@/contexts/OrganizationContext';
import { getOrgSlug } from '../utils/tenant';

export function AuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { updateOrganization } = useOrganization();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get('token');
    if (!token) {
      setError('No authentication token provided');
      return;
    }

    const handleCallback = async () => {
      try {
        const orgSlug = getOrgSlug();
        const response = await exchangeToken(token, orgSlug);

        setAuthToken(response.access_token);
        setAuthUser({ user_id: response.user_id, email: response.email });
        updateOrganization(response.organization);

        navigate('/home');
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Authentication failed');
      }
    };

    handleCallback();
  }, [searchParams, navigate, updateOrganization]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-destructive">{error}</p>
          <a href="/login" className="text-sm underline">Return to login</a>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">Authenticating...</p>
    </div>
  );
}
