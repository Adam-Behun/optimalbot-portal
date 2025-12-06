from enum import Enum


class SessionStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class CallStatus(str, Enum):
    NOT_STARTED = "Not Started"
    DIALING = "Dialing"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    FAILED = "Failed"
    SUPERVISOR_DIALED = "Supervisor Dialed"
    VOICEMAIL = "Voicemail"


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    LOCKED = "locked"
    INACTIVE = "inactive"


class AuditEventType(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET = "password_reset"
    PHI_ACCESS = "phi_access"
    API_ACCESS = "api_access"


class PHIAction(str, Enum):
    VIEW = "view"
    VIEW_LIST = "view_list"
    CREATE = "create"
    CREATE_BULK = "create_bulk"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"
    START_CALL = "start_call"
    END_CALL = "end_call"
    VIEW_STATUS = "view_status"
    VIEW_TRANSCRIPT = "view_transcript"


class ResourceType(str, Enum):
    PATIENT = "patient"
    CALL = "call"
    TRANSCRIPT = "transcript"