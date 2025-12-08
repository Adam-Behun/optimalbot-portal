import { useEffect } from 'react';

const LOGIN_URL = import.meta.env.VITE_LOGIN_URL || 'https://optimalbot.ai/login';

function getMarketingUrl(path: string): string {
  // Extract base URL from login URL (e.g., http://localhost:4321/login -> http://localhost:4321)
  const baseUrl = LOGIN_URL.replace(/\/login$/, '');
  return `${baseUrl}${path}`;
}

export function LoginRedirect() {
  useEffect(() => {
    window.location.href = LOGIN_URL;
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">Redirecting to login...</p>
    </div>
  );
}

export function ForgotPasswordRedirect() {
  useEffect(() => {
    window.location.href = getMarketingUrl('/forgot-password');
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">Redirecting...</p>
    </div>
  );
}

export function ResetPasswordRedirect() {
  useEffect(() => {
    // Preserve query params (token, email)
    const params = window.location.search;
    window.location.href = getMarketingUrl('/reset-password') + params;
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">Redirecting...</p>
    </div>
  );
}
