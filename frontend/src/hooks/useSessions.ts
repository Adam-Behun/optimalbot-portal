import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { getSessions, getSession, deleteSession } from '@/api';
import { Session } from '@/types';

export function useSessions(workflow: string) {
  return useQuery({
    queryKey: ['sessions', workflow],
    queryFn: () => getSessions(workflow),

    // Dynamic polling: only when active sessions exist
    refetchInterval: (query) => {
      const sessions = query.state.data as Session[] | undefined;
      const hasActive = sessions?.some(
        s => s.status === 'starting' || s.status === 'running'
      );
      return hasActive ? 3000 : false;
    },
  });
}

export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => getSession(sessionId!),
    enabled: !!sessionId,
  });
}

export function useDeleteSession(workflow: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => deleteSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions', workflow] });
    },
  });
}

export function useDeleteSessions(workflow: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (sessionIds: string[]) => {
      const results = await Promise.allSettled(
        sessionIds.map(id => deleteSession(id))
      );
      const successCount = results.filter(r => r.status === 'fulfilled').length;
      const failCount = results.filter(r => r.status === 'rejected').length;
      return { successCount, failCount };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions', workflow] });
    },
  });
}
