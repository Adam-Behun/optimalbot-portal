import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  uploadRecordings,
  getOnboardingStatus,
  transcribeRecordings,
  getOnboardingConversations,
  updateOnboardingConversation,
  approveOnboardingConversation,
  deleteOnboardingConversation,
  OnboardingStatus,
  TranscribeResponse,
  OnboardingConversation,
  ConversationUtterance,
  ConversationMetadata,
} from '@/api';
import {
  Upload,
  FileAudio,
  CheckCircle,
  Circle,
  Loader2,
  RefreshCw,
  X,
  Pencil,
  Check,
  Trash2,
  Eye,
  Plus,
  MessageSquare,
} from 'lucide-react';
import { toast } from 'sonner';

const MAX_FILE_SIZE_MB = 500;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

const filterMp3Files = (fileList: FileList | File[]): File[] =>
  Array.from(fileList).filter((f) => f.name.toLowerCase().endsWith('.mp3'));

export function AdminOnboarding() {
  const [org, setOrg] = useState('');
  const [workflow, setWorkflow] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [transcribeResult, setTranscribeResult] = useState<TranscribeResponse | null>(null);

  // Conversation state
  const [conversations, setConversations] = useState<OnboardingConversation[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [editingConversation, setEditingConversation] = useState<OnboardingConversation | null>(
    null
  );
  const [editedRoles, setEditedRoles] = useState<Record<string, string>>({});
  const [editedUtterances, setEditedUtterances] = useState<ConversationUtterance[]>([]);
  const [editedMetadata, setEditedMetadata] = useState<ConversationMetadata>({});
  const [saving, setSaving] = useState(false);

  // Delete confirmation state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [conversationToDelete, setConversationToDelete] = useState<string | null>(null);

  const addFilesWithValidation = useCallback((newFiles: File[]) => {
    const mp3Files = filterMp3Files(newFiles);
    const validFiles: File[] = [];
    const oversizedFiles: string[] = [];

    for (const file of mp3Files) {
      if (file.size > MAX_FILE_SIZE_BYTES) {
        oversizedFiles.push(file.name);
      } else {
        validFiles.push(file);
      }
    }

    if (oversizedFiles.length > 0) {
      toast.error(`Files exceed ${MAX_FILE_SIZE_MB}MB limit: ${oversizedFiles.join(', ')}`);
    }

    if (validFiles.length > 0) {
      setFiles((prev) => [...prev, ...validFiles]);
    }
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFilesWithValidation(Array.from(e.target.files));
    }
  };


  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      addFilesWithValidation(Array.from(e.dataTransfer.files));
    },
    [addFilesWithValidation]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!org || !workflow) return;

    try {
      const result = await getOnboardingStatus(org, workflow);
      setStatus(result);
    } catch (error) {
      setStatus(null);
      toast.error(`Failed to load status: ${(error as Error).message}`);
    }
  }, [org, workflow]);

  const fetchConversations = useCallback(async () => {
    if (!org || !workflow) return;

    setLoadingConversations(true);
    try {
      const result = await getOnboardingConversations(org, workflow);
      setConversations(result);
    } catch (error) {
      setConversations([]);
      // Don't show error if just no conversations yet
      if ((error as Error).message.includes('404')) {
        return;
      }
      toast.error(`Failed to load conversations: ${(error as Error).message}`);
    } finally {
      setLoadingConversations(false);
    }
  }, [org, workflow]);

  const handleUpload = async () => {
    if (!org || !workflow) {
      toast.error('Please enter organization and workflow names');
      return;
    }
    if (files.length === 0) {
      toast.error('Please select at least one MP3 file');
      return;
    }

    setUploading(true);
    try {
      const result = await uploadRecordings(org, workflow, files);
      toast.success(`Uploaded ${result.uploaded_files.length} files`);
      setFiles([]);
      await refreshStatus();
    } catch (error) {
      toast.error(`Upload failed: ${(error as Error).message}`);
    } finally {
      setUploading(false);
    }
  };

  // Fetch conversations when org/workflow changes and status exists
  useEffect(() => {
    if (org && workflow && status) {
      fetchConversations();
    }
  }, [org, workflow, status, fetchConversations]);

  const handleTranscribe = async () => {
    if (!org || !workflow) {
      toast.error('Please enter organization and workflow names');
      return;
    }

    setTranscribing(true);
    setTranscribeResult(null);
    try {
      const result = await transcribeRecordings(org, workflow);
      setTranscribeResult(result);
      if (result.error_count === 0) {
        toast.success(`Transcribed ${result.success_count} files successfully`);
      } else {
        toast.warning(`Transcribed ${result.success_count} files, ${result.error_count} errors`);
      }
      await refreshStatus();
    } catch (error) {
      toast.error(`Transcription failed: ${(error as Error).message}`);
    } finally {
      setTranscribing(false);
    }
  };

  const calculateProgress = (): number => {
    if (!status) return 0;
    const phaseList = Object.values(status.phases);
    const completed = phaseList.filter((p) => p.complete).length;
    return (completed / phaseList.length) * 100;
  };

  // Conversation editing functions
  const openEditor = (conversation: OnboardingConversation) => {
    setEditingConversation(conversation);
    setEditedRoles({ ...conversation.roles });
    setEditedUtterances([...conversation.conversation]);
    setEditedMetadata(conversation.metadata ? { ...conversation.metadata } : {});
  };

  const closeEditor = () => {
    setEditingConversation(null);
    setEditedRoles({});
    setEditedUtterances([]);
    setEditedMetadata({});
  };

  const updateUtterance = (index: number, field: 'role' | 'text', value: string) => {
    setEditedUtterances((prev) =>
      prev.map((u, i) => (i === index ? { ...u, [field]: value } : u))
    );
  };

  const deleteUtterance = (index: number) => {
    setEditedUtterances((prev) => prev.filter((_, i) => i !== index));
  };

  const addUtterance = () => {
    const defaultRole = Object.keys(editedRoles)[0] || 'speaker';
    setEditedUtterances((prev) => [...prev, { role: defaultRole, text: '' }]);
  };

  const handleSave = async () => {
    if (!editingConversation) return;

    setSaving(true);
    try {
      await updateOnboardingConversation(editingConversation.id, {
        roles: editedRoles,
        conversation: editedUtterances,
        metadata: editedMetadata,
      });
      toast.success('Conversation saved');
      await fetchConversations();
      closeEditor();
    } catch (error) {
      toast.error(`Failed to save: ${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndApprove = async () => {
    if (!editingConversation) return;

    setSaving(true);
    try {
      await updateOnboardingConversation(editingConversation.id, {
        roles: editedRoles,
        conversation: editedUtterances,
        metadata: editedMetadata,
      });
      await approveOnboardingConversation(editingConversation.id);
      toast.success('Conversation approved');
      await fetchConversations();
      closeEditor();
    } catch (error) {
      toast.error(`Failed to approve: ${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleApprove = async (conversationId: string) => {
    try {
      await approveOnboardingConversation(conversationId);
      toast.success('Conversation approved');
      await fetchConversations();
    } catch (error) {
      toast.error(`Failed to approve: ${(error as Error).message}`);
    }
  };

  const openDeleteDialog = (conversationId: string) => {
    setConversationToDelete(conversationId);
    setDeleteDialogOpen(true);
  };

  const handleConfirmDelete = async () => {
    if (!conversationToDelete) return;

    try {
      await deleteOnboardingConversation(conversationToDelete);
      toast.success('Conversation deleted');
      await fetchConversations();
    } catch (error) {
      toast.error(`Failed to delete: ${(error as Error).message}`);
    } finally {
      setDeleteDialogOpen(false);
      setConversationToDelete(null);
    }
  };

  const roleOptions = Object.keys(editedRoles);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Client Onboarding</h1>
      </div>

      {/* Configuration */}
      <Card>
        <CardHeader>
          <CardTitle>Workflow Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="org">Organization Name</Label>
              <Input
                id="org"
                placeholder="e.g., demo_clinic_alpha"
                value={org}
                onChange={(e) => setOrg(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="workflow">Workflow Name</Label>
              <Input
                id="workflow"
                placeholder="e.g., eligibility_verification"
                value={workflow}
                onChange={(e) => setWorkflow(e.target.value)}
              />
            </div>
          </div>
          <Button variant="outline" onClick={refreshStatus} disabled={!org || !workflow}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Check Status
          </Button>
        </CardContent>
      </Card>

      {/* Status Display */}
      {status && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Pipeline Status</span>
              <Badge variant="outline">{status.path}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Progress value={calculateProgress()} className="h-2" />
            <div className="grid grid-cols-5 gap-4 text-center">
              <PhaseIndicator
                label="Recordings"
                complete={status.phases.recordings.complete}
                count={status.phases.recordings.count}
              />
              <PhaseIndicator
                label="Transcripts"
                complete={status.phases.transcripts.complete}
                count={status.phases.transcripts.count}
              />
              <PhaseIndicator
                label="Samples"
                complete={status.phases.sample_conversations.complete}
                count={status.phases.sample_conversations.count}
                approved={status.phases.sample_conversations.approved}
              />
              <PhaseIndicator label="Flow Design" complete={status.phases.flow_design.complete} />
              <PhaseIndicator
                label="Code Gen"
                complete={status.phases.code_generation.complete}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* File Upload */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5" />
            Upload Recordings
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            className="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:border-primary transition-colors"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onClick={() => document.getElementById('file-input')?.click()}
          >
            <FileAudio className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
            <p className="text-muted-foreground mb-2">
              Drag and drop MP3 files here, or click to select
            </p>
            <p className="text-sm text-muted-foreground">
              MP3 files only, max {MAX_FILE_SIZE_MB}MB each
            </p>
            <input
              id="file-input"
              type="file"
              multiple
              accept=".mp3,audio/mpeg"
              className="hidden"
              onChange={handleFileChange}
            />
          </div>

          {files.length > 0 && (
            <div className="space-y-2">
              <Label>Selected Files ({files.length})</Label>
              <div className="max-h-40 overflow-y-auto border rounded-md p-2 space-y-1">
                {files.map((file) => (
                  <div
                    key={file.name}
                    className="flex items-center justify-between p-2 bg-muted rounded-md"
                  >
                    <span className="text-sm truncate">{file.name}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setFiles((prev) => prev.filter((f) => f.name !== file.name))}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <Button
            onClick={handleUpload}
            disabled={uploading || files.length === 0 || !org || !workflow}
          >
            {uploading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Uploading...
              </>
            ) : (
              <>
                <Upload className="mr-2 h-4 w-4" />
                Upload {files.length} File{files.length !== 1 ? 's' : ''}
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* Transcription */}
      <Card>
        <CardHeader>
          <CardTitle>Transcription</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground">
            After uploading recordings, run transcription with speaker diarization to generate JSON
            transcripts.
          </p>
          <Button
            onClick={handleTranscribe}
            disabled={
              transcribing || !org || !workflow || !status?.phases.recordings.complete
            }
          >
            {transcribing ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Transcribing...
              </>
            ) : (
              'Start Transcription'
            )}
          </Button>

          {transcribeResult && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center gap-2">
                <Badge
                  variant={transcribeResult.error_count === 0 ? 'default' : 'destructive'}
                >
                  {transcribeResult.success_count}/{transcribeResult.total_files} successful
                </Badge>
              </div>
              <div className="text-sm space-y-1">
                {transcribeResult.results.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {r.status === 'success' ? (
                      <CheckCircle className="h-4 w-4 text-green-500" />
                    ) : (
                      <X className="h-4 w-4 text-red-500" />
                    )}
                    <span>{r.file}</span>
                    {r.error && <span className="text-red-500 text-xs">({r.error})</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Sample Conversations */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" />
            Sample Conversations
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground">
            Review and edit cleaned conversations before using them to generate flow definitions.
          </p>

          {loadingConversations ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading conversations...
            </div>
          ) : conversations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No conversations yet. Run the cleanup agent after transcription to create sample
              conversations.
            </p>
          ) : (
            <div className="border rounded-md divide-y">
              {conversations.map((conv) => (
                <div
                  key={conv.id}
                  className="flex items-center justify-between p-3 hover:bg-muted/50"
                >
                  <div className="flex items-center gap-3">
                    <FileAudio className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium">{conv.source_filename}</span>
                    <Badge variant={conv.status === 'approved' ? 'default' : 'secondary'}>
                      {conv.status}
                      {conv.status === 'approved' && (
                        <CheckCircle className="ml-1 h-3 w-3" />
                      )}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {conv.conversation.length} utterances
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {conv.status === 'approved' ? (
                      <Button variant="ghost" size="sm" onClick={() => openEditor(conv)}>
                        <Eye className="h-4 w-4" />
                      </Button>
                    ) : (
                      <>
                        <Button variant="ghost" size="sm" onClick={() => openEditor(conv)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleApprove(conv.id)}
                        >
                          <Check className="h-4 w-4 text-green-500" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openDeleteDialog(conv.id)}
                        >
                          <Trash2 className="h-4 w-4 text-red-500" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          <Button
            variant="outline"
            onClick={fetchConversations}
            disabled={!org || !workflow || loadingConversations}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </CardContent>
      </Card>

      {/* Conversation Editor Modal */}
      <Dialog open={!!editingConversation} onOpenChange={(open: boolean) => !open && closeEditor()}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>
              {editingConversation?.status === 'approved' ? 'View' : 'Edit'}:{' '}
              {editingConversation?.source_filename}
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-6 py-4">
            {/* Roles Section */}
            <div className="space-y-3">
              <Label className="text-base font-semibold">Roles</Label>
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(editedRoles).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2">
                    <Label className="w-32 text-sm text-muted-foreground">{key}:</Label>
                    <Input
                      value={value}
                      onChange={(e) =>
                        setEditedRoles((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                      disabled={editingConversation?.status === 'approved'}
                      className="flex-1"
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Conversation Section */}
            <div className="space-y-3">
              <Label className="text-base font-semibold">Conversation</Label>
              {roleOptions.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No roles defined. Add roles above first.
                </p>
              )}
              <div className="border rounded-md divide-y max-h-[400px] overflow-y-auto">
                {editedUtterances.map((utterance, index) => (
                  <div key={index} className="flex items-start gap-2 p-2">
                    <Select
                      value={utterance.role}
                      onValueChange={(value) => updateUtterance(index, 'role', value)}
                      disabled={editingConversation?.status === 'approved'}
                    >
                      <SelectTrigger className="w-40">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {roleOptions.map((role) => (
                          <SelectItem key={role} value={role}>
                            {role}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Textarea
                      value={utterance.text}
                      onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                        updateUtterance(index, 'text', e.target.value)
                      }
                      disabled={editingConversation?.status === 'approved'}
                      className="flex-1 min-h-[60px]"
                      rows={2}
                    />
                    {editingConversation?.status !== 'approved' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteUtterance(index)}
                        className="text-red-500"
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              {editingConversation?.status !== 'approved' && roleOptions.length > 0 && (
                <Button variant="outline" size="sm" onClick={addUtterance}>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Utterance
                </Button>
              )}
            </div>

            {/* Metadata Section */}
            <div className="space-y-3">
              <Label className="text-base font-semibold">Metadata</Label>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-sm text-muted-foreground">Call Type</Label>
                  <Input
                    value={editedMetadata.call_type || ''}
                    onChange={(e) =>
                      setEditedMetadata((prev) => ({ ...prev, call_type: e.target.value }))
                    }
                    disabled={editingConversation?.status === 'approved'}
                    placeholder="e.g., eligibility_verification"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-sm text-muted-foreground">Insurance Company</Label>
                  <Input
                    value={editedMetadata.insurance_company || ''}
                    onChange={(e) =>
                      setEditedMetadata((prev) => ({
                        ...prev,
                        insurance_company: e.target.value,
                      }))
                    }
                    disabled={editingConversation?.status === 'approved'}
                    placeholder="e.g., UnitedHealthcare"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-sm text-muted-foreground">Practice Name</Label>
                  <Input
                    value={editedMetadata.practice_name || ''}
                    onChange={(e) =>
                      setEditedMetadata((prev) => ({
                        ...prev,
                        practice_name: e.target.value,
                      }))
                    }
                    disabled={editingConversation?.status === 'approved'}
                    placeholder="e.g., Synaptrex Suite PELC"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-sm text-muted-foreground">Outcome</Label>
                  <Select
                    value={editedMetadata.outcome || ''}
                    onValueChange={(value) =>
                      setEditedMetadata((prev) => ({ ...prev, outcome: value }))
                    }
                    disabled={editingConversation?.status === 'approved'}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select outcome" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="successful">Successful</SelectItem>
                      <SelectItem value="unsuccessful">Unsuccessful</SelectItem>
                      <SelectItem value="partial">Partial</SelectItem>
                      <SelectItem value="transferred">Transferred</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={closeEditor}>
              {editingConversation?.status === 'approved' ? 'Close' : 'Cancel'}
            </Button>
            {editingConversation?.status !== 'approved' && (
              <>
                <Button variant="outline" onClick={handleSave} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Save
                </Button>
                <Button onClick={handleSaveAndApprove} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Save & Approve
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Conversation</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete this conversation? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setConversationToDelete(null)}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-red-600 hover:bg-red-700"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PhaseIndicator({
  label,
  complete,
  count,
  approved,
}: {
  label: string;
  complete: boolean;
  count?: number;
  approved?: number;
}) {
  return (
    <div className="flex flex-col items-center gap-1">
      {complete ? (
        <CheckCircle className="h-6 w-6 text-green-500" />
      ) : (
        <Circle className="h-6 w-6 text-muted-foreground" />
      )}
      <span className="text-sm font-medium">{label}</span>
      {count !== undefined && approved !== undefined ? (
        <span className="text-xs text-muted-foreground">
          {approved}/{count} approved
        </span>
      ) : count !== undefined ? (
        <span className="text-xs text-muted-foreground">{count} files</span>
      ) : null}
    </div>
  );
}
