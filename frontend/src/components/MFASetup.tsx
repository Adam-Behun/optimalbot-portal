import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Shield, ShieldCheck, ShieldOff, Copy, Check, AlertCircle, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  getMFAStatus,
  setupMFA,
  verifyMFA,
  disableMFA,
  regenerateBackupCodes,
  MFASetupResponse,
  MFAStatusResponse,
} from '@/api';

type SetupStep = 'init' | 'scan' | 'verify' | 'backup' | 'done';

export function MFASetup() {
  const [status, setStatus] = useState<MFAStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState<SetupStep>('init');
  const [setupData, setSetupData] = useState<MFASetupResponse | null>(null);
  const [verifyCode, setVerifyCode] = useState('');
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [disablePassword, setDisablePassword] = useState('');
  const [showDisable, setShowDisable] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [copied, setCopied] = useState(false);

  // Fetch MFA status on mount
  useEffect(() => {
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    try {
      setLoading(true);
      const data = await getMFAStatus();
      setStatus(data);
    } catch (err) {
      console.error('Failed to fetch MFA status:', err);
      toast.error('Failed to load MFA status');
    } finally {
      setLoading(false);
    }
  };

  const handleStartSetup = async () => {
    try {
      setProcessing(true);
      const data = await setupMFA();
      setSetupData(data);
      setStep('scan');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to start MFA setup');
    } finally {
      setProcessing(false);
    }
  };

  const handleVerify = async () => {
    if (verifyCode.length !== 6) {
      toast.error('Please enter a 6-digit code');
      return;
    }

    try {
      setProcessing(true);
      const result = await verifyMFA(verifyCode);
      if (result.success) {
        setBackupCodes(result.backup_codes);
        setStep('backup');
        toast.success('MFA enabled successfully');
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Invalid verification code');
    } finally {
      setProcessing(false);
    }
  };

  const handleDisable = async () => {
    if (!disablePassword) {
      toast.error('Please enter your password');
      return;
    }

    try {
      setProcessing(true);
      await disableMFA(disablePassword);
      setStatus({ enabled: false, backup_codes_remaining: 0 });
      setShowDisable(false);
      setDisablePassword('');
      toast.success('MFA has been disabled');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to disable MFA');
    } finally {
      setProcessing(false);
    }
  };

  const handleRegenerateBackupCodes = async () => {
    try {
      setProcessing(true);
      const result = await regenerateBackupCodes();
      if (result.success) {
        setBackupCodes(result.backup_codes);
        setStep('backup');
        toast.success('Backup codes regenerated');
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to regenerate backup codes');
    } finally {
      setProcessing(false);
    }
  };

  const copyBackupCodes = () => {
    const text = backupCodes.join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    toast.success('Backup codes copied to clipboard');
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDone = () => {
    setStep('init');
    setSetupData(null);
    setVerifyCode('');
    setBackupCodes([]);
    fetchStatus();
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Loading MFA status...</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  // MFA is enabled - show status
  if (status?.enabled && step === 'init') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-green-500" />
            <CardTitle>Two-Factor Authentication</CardTitle>
          </div>
          <CardDescription>
            Your account is protected with 2FA
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-3 bg-green-500/10 rounded-lg">
            <div className="flex items-center gap-2">
              <Badge variant="default" className="bg-green-600">Enabled</Badge>
              <span className="text-sm">Two-factor authentication is active</span>
            </div>
          </div>

          <div className="text-sm text-muted-foreground">
            Backup codes remaining: <strong>{status.backup_codes_remaining}</strong>
          </div>

          {status.backup_codes_remaining <= 2 && (
            <div className="flex items-center gap-2 p-3 bg-amber-500/10 rounded-lg text-amber-600 dark:text-amber-400">
              <AlertCircle className="h-4 w-4" />
              <span className="text-sm">Low backup codes. Consider regenerating.</span>
            </div>
          )}
        </CardContent>
        <CardFooter className="flex gap-2">
          <Button
            variant="outline"
            onClick={handleRegenerateBackupCodes}
            disabled={processing}
          >
            {processing ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Regenerate Backup Codes
          </Button>
          <Button
            variant="destructive"
            onClick={() => setShowDisable(true)}
          >
            Disable 2FA
          </Button>
        </CardFooter>

        {/* Disable confirmation */}
        {showDisable && (
          <CardContent className="border-t pt-4">
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Enter your password to disable two-factor authentication:
              </p>
              <div className="flex gap-2">
                <Input
                  type="password"
                  placeholder="Your password"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                />
                <Button
                  variant="destructive"
                  onClick={handleDisable}
                  disabled={processing}
                >
                  {processing ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Confirm'}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowDisable(false);
                    setDisablePassword('');
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          </CardContent>
        )}
      </Card>
    );
  }

  // Step: Init - Show enable button
  if (step === 'init') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            <CardTitle>Two-Factor Authentication</CardTitle>
          </div>
          <CardDescription>
            Add an extra layer of security to your account
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2 p-3 bg-muted rounded-lg">
            <ShieldOff className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">Two-factor authentication is not enabled</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Protect your account by requiring a verification code from your authenticator app
            in addition to your password when signing in.
          </p>
        </CardContent>
        <CardFooter>
          <Button onClick={handleStartSetup} disabled={processing}>
            {processing ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Shield className="h-4 w-4 mr-2" />}
            Enable 2FA
          </Button>
        </CardFooter>
      </Card>
    );
  }

  // Step: Scan QR code
  if (step === 'scan' && setupData) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Scan QR Code</CardTitle>
          <CardDescription>
            Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex justify-center">
            <img
              src={setupData.qr_code}
              alt="MFA QR Code"
              className="w-48 h-48 border rounded-lg"
            />
          </div>
          <div className="text-center">
            <p className="text-xs text-muted-foreground mb-2">
              Can't scan? Enter this code manually:
            </p>
            <code className="text-sm bg-muted px-3 py-1 rounded font-mono">
              {setupData.secret}
            </code>
          </div>
        </CardContent>
        <CardFooter className="flex justify-between">
          <Button variant="outline" onClick={() => setStep('init')}>
            Back
          </Button>
          <Button onClick={() => setStep('verify')}>
            Next
          </Button>
        </CardFooter>
      </Card>
    );
  }

  // Step: Verify code
  if (step === 'verify') {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Verify Setup</CardTitle>
          <CardDescription>
            Enter the 6-digit code from your authenticator app to verify setup
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="verify-code">Verification Code</Label>
            <Input
              id="verify-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              placeholder="000000"
              value={verifyCode}
              onChange={(e) => setVerifyCode(e.target.value.replace(/\D/g, ''))}
              className="text-center text-2xl tracking-widest font-mono"
            />
          </div>
        </CardContent>
        <CardFooter className="flex justify-between">
          <Button variant="outline" onClick={() => setStep('scan')}>
            Back
          </Button>
          <Button onClick={handleVerify} disabled={processing || verifyCode.length !== 6}>
            {processing ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Verify & Enable
          </Button>
        </CardFooter>
      </Card>
    );
  }

  // Step: Show backup codes
  if (step === 'backup') {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-green-500" />
            <CardTitle>Save Your Backup Codes</CardTitle>
          </div>
          <CardDescription>
            Store these codes securely. You can use them to access your account if you lose your authenticator.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted p-4 rounded-lg">
            <div className="grid grid-cols-2 gap-2 font-mono text-sm">
              {backupCodes.map((code, i) => (
                <div key={i} className="bg-background px-3 py-2 rounded text-center">
                  {code}
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 p-3 bg-amber-500/10 rounded-lg text-amber-600 dark:text-amber-400">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            <span className="text-sm">
              Each code can only be used once. Keep them safe!
            </span>
          </div>
        </CardContent>
        <CardFooter className="flex justify-between">
          <Button variant="outline" onClick={copyBackupCodes}>
            {copied ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
            {copied ? 'Copied!' : 'Copy Codes'}
          </Button>
          <Button onClick={handleDone}>
            Done
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return null;
}
