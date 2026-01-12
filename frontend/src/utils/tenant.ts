export const getSubdomain = (): string => {
  const hostname = window.location.hostname;
  const port = window.location.port;

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    if (port === '3001') {
      return 'demo-clinic-beta';
    }
    return 'demo-clinic-alpha';
  }

  const parts = hostname.split('.');
  if (parts.length >= 3) {
    return parts[0];
  }

  return parts[0];
};
