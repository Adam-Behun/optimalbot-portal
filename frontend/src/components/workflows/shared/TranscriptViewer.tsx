import { TranscriptMessage } from '@/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface TranscriptViewerProps {
  messages: TranscriptMessage[];
  summary?: string;
}

// Get display name for role
function getRoleLabel(role: TranscriptMessage['role']): string {
  switch (role) {
    case 'assistant':
      return 'Bot';
    case 'user':
      return 'Caller';
    case 'system':
      return 'System';
    default:
      return role;
  }
}

// Format timestamp for display
function formatTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString();
}

export function TranscriptViewer({ messages, summary }: TranscriptViewerProps) {
  if (messages.length === 0) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="text-muted-foreground">No transcript available</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {summary && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base">Summary</CardTitle>
          </CardHeader>
          <CardContent className="py-3">
            <p className="text-sm">{summary}</p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {messages.map((message, index) => (
          <div
            key={index}
            className={`flex flex-col rounded-lg p-3 ${
              message.role === 'assistant'
                ? 'bg-blue-50 dark:bg-blue-950'
                : message.role === 'system'
                ? 'bg-yellow-50 dark:bg-yellow-950'
                : 'bg-gray-100 dark:bg-gray-800'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-muted-foreground">
                {getRoleLabel(message.role)}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatTimestamp(message.timestamp)}
              </span>
            </div>
            <p className="text-sm">{message.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
