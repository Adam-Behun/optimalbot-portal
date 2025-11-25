import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { requestPasswordReset } from '../api';

export function ForgotPasswordForm() {
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [resetToken, setResetToken] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      const response = await requestPasswordReset(email);
      setResetToken(response.token);
      toast.success(`Reset token generated! Expires in ${response.expires_in_minutes} minutes.`);
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || 'Failed to generate reset token';
      toast.error(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Forgot Password</CardTitle>
        <CardDescription>Enter your email to receive a reset token</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={isLoading || !!resetToken}
            />
          </div>

          {resetToken && (
            <div className="grid gap-2">
              <Label htmlFor="token">Reset Token</Label>
              <Input
                id="token"
                type="text"
                value={resetToken}
                readOnly
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                Copy this token and use it on the reset password page
              </p>
            </div>
          )}

          {!resetToken && (
            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading ? 'Generating...' : 'Generate Reset Token'}
            </Button>
          )}

          {resetToken && (
            <Button asChild className="w-full">
              <Link to="/reset-password">Go to Reset Password</Link>
            </Button>
          )}

          <div className="text-center text-sm">
            <Link to="/login" className="underline">
              Back to Login
            </Link>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
