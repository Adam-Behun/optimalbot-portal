import { useState } from 'react';
import { TranscriptMessage } from '@/types';
import { ChevronDown, ChevronRight, PhoneForwarded } from 'lucide-react';

interface TranscriptViewerProps {
  messages: TranscriptMessage[];
  summary?: string;
  /** Label for non-agent speaker. Defaults to 'Insurance Representative' for outbound calls */
  callerLabel?: string;
}

export function TranscriptViewer({ messages, summary, callerLabel = 'Insurance Representative' }: TranscriptViewerProps) {
  const [showIVRDetails, setShowIVRDetails] = useState(false);

  if (messages.length === 0) {
    return (
      <p className="text-muted-foreground">No transcript available</p>
    );
  }

  // Separate IVR messages from regular conversation
  const ivrMessages = messages.filter(m => m.type === 'ivr' || m.type === 'ivr_action');
  const ivrSummary = messages.find(m => m.type === 'ivr_summary' && (m.content.includes('ompleted') || m.content.includes('ailed')));
  const conversationMessages = messages.filter(m => m.type === 'transcript' || m.type === 'transfer');

  return (
    <div className="space-y-4">
      {/* Summary if provided */}
      {summary && (
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900 border">
          <span className="text-sm font-semibold">Summary: </span>
          <span className="text-sm">{summary}</span>
        </div>
      )}

      {/* IVR Navigation Summary */}
      {ivrSummary && (
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">
                IVR Navigation:
              </span>
              <span className={`text-sm font-medium ${
                ivrSummary.content.includes('ompleted')
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-red-600 dark:text-red-400'
              }`}>
                {ivrSummary.content}
              </span>
            </div>
            {ivrMessages.length > 0 && (
              <button
                onClick={() => setShowIVRDetails(!showIVRDetails)}
                className="flex items-center gap-1 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100"
              >
                {showIVRDetails ? (
                  <>
                    <ChevronDown size={16} />
                    Hide Details
                  </>
                ) : (
                  <>
                    <ChevronRight size={16} />
                    Show Details
                  </>
                )}
              </button>
            )}
          </div>

          {/* IVR Details Dropdown */}
          {showIVRDetails && ivrMessages.length > 0 && (
            <div className="mt-3 space-y-2 max-h-[300px] overflow-y-auto">
              {ivrMessages.map((message, index) => (
                <div
                  key={index}
                  className={`p-2 rounded border-l-2 text-sm ${
                    message.type === 'ivr_action'
                      ? 'bg-amber-50 dark:bg-amber-950 border-amber-500 font-medium'
                      : 'bg-slate-100 dark:bg-slate-800 border-slate-400'
                  }`}
                >
                  {message.content}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Main Conversation Transcript */}
      {conversationMessages.length > 0 && (
        <div className="space-y-2 max-h-[600px] overflow-y-auto">
          {conversationMessages.map((message, index) => {
            // Handle transfer events
            if (message.type === 'transfer') {
              return (
                <div
                  key={index}
                  className="p-3 rounded-lg bg-purple-50 dark:bg-purple-950 border-l-4 border-purple-500"
                >
                  <div className="flex items-center gap-2">
                    <PhoneForwarded className="h-4 w-4 text-purple-600 dark:text-purple-400" />
                    <span className="font-semibold text-purple-900 dark:text-purple-100">
                      {message.content}
                    </span>
                  </div>
                  <div className="text-xs text-purple-600 dark:text-purple-400 mt-1">
                    {new Date(message.timestamp).toLocaleTimeString()}
                  </div>
                </div>
              );
            }

            // Handle regular conversation messages
            const isAgent = message.role === 'assistant';
            return (
              <div
                key={index}
                className={`flex ${isAgent ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`p-2.5 rounded-lg border-l-4 w-2/3 ${
                    isAgent
                      ? 'bg-primary border-primary text-primary-foreground'
                      : 'bg-secondary border-secondary text-secondary-foreground'
                  }`}
                >
                  <div className="font-semibold text-sm mb-1.5">
                    {isAgent ? 'Provider Agent' : callerLabel}
                  </div>
                  <div className="text-sm">
                    {message.content}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {conversationMessages.length === 0 && !ivrSummary && (
        <p className="text-muted-foreground">No conversation transcript available.</p>
      )}
    </div>
  );
}
