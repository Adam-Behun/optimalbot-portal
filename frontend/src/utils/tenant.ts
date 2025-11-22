// Tenant/organization utilities

/**
 * Extract organization slug from hostname/port
 * - localhost:3000 → "demo_clinic_alpha"
 * - localhost:3001 → "demo_clinic_beta"
 * - Production: subdomain.datasova.com → subdomain (e.g., "demo_clinic_alpha")
 *
 * Note: The subdomain must exactly match the org slug in the database.
 * For example: demo_clinic_alpha.datasova.com → "demo_clinic_alpha"
 */
export const getOrgSlug = (): string => {
  const hostname = window.location.hostname;
  const port = window.location.port;

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    // Port-based routing for local development
    if (port === '3001') {
      return 'demo_clinic_beta';
    }
    return 'demo_clinic_alpha'; // Default (port 3000)
  }

  // Extract subdomain directly - it should match the org slug in database
  // e.g., demo_clinic_alpha.datasova.com → "demo_clinic_alpha"
  const subdomain = hostname.split('.')[0];
  return subdomain;
};
