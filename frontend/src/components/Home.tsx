import { Navigation } from './Navigation';
import { getOrganization } from '@/lib/auth';

export function Home() {
  const org = getOrganization();

  return (
    <>
      <Navigation />
      <div className="max-w-4xl mx-auto py-8 px-4">
        <h1 className="text-3xl font-bold">{org?.name || 'Welcome'}</h1>
      </div>
    </>
  );
}
