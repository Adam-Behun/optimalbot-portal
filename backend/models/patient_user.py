import os
import logging
from datetime import datetime
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AsyncPatientRecord:
    """Async database operations for patient records"""

    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[os.getenv("MONGO_DB_NAME", "alfons")]
        self.patients = self.db.patients

    async def _ensure_indexes(self):
        """Create indexes for patient collection"""
        try:
            await self.patients.create_index([("organization_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def find_patient_by_id(self, patient_id: str, organization_id: str = None) -> Optional[dict]:
        """Find patient by MongoDB ObjectId, optionally filtered by organization"""
        try:
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            patient = await self.patients.find_one(query)
            return patient
        except Exception as e:
            logger.error(f"Error finding patient {patient_id}: {e}")
            return None

    async def find_patients_by_organization(self, organization_id: str, workflow: str = None) -> List[dict]:
        """Find all patients for an organization, optionally filtered by workflow"""
        try:
            query = {"organization_id": ObjectId(organization_id)}
            if workflow:
                query["workflow"] = workflow
            cursor = self.patients.find(query).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding patients for org {organization_id}: {e}")
            return []

    async def find_patients_by_status(self, status: str, organization_id: str = None) -> List[dict]:
        """Find all patients with a specific prior auth status"""
        try:
            query = {"prior_auth_status": status}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            cursor = self.patients.find(query)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding patients with status {status}: {e}")
            return []

    async def add_patient(self, patient_data: dict) -> Optional[str]:
        """Insert a new patient record with flat fields"""
        try:
            await self._ensure_indexes()

            now = datetime.utcnow().isoformat()

            # Convert organization_id to ObjectId if provided as string
            if "organization_id" in patient_data and isinstance(patient_data["organization_id"], str):
                patient_data["organization_id"] = ObjectId(patient_data["organization_id"])

            # Add system fields
            patient_data.update({
                "created_at": now,
                "updated_at": now,
                "call_status": patient_data.get("call_status", "Not Started")
            })

            result = await self.patients.insert_one(patient_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error adding patient: {e}")
            return None

    async def update_patient(self, patient_id: str, update_fields: dict, organization_id: str = None) -> bool:
        """Generic update method for any patient fields"""
        try:
            update_fields["updated_at"] = datetime.utcnow().isoformat()

            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)

            result = await self.patients.update_one(
                query,
                {"$set": update_fields}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating patient {patient_id}: {e}")
            return False
    
    async def update_field(
        self,
        patient_id: str,
        field_key: str,
        value: any,
        organization_id: str = None
    ) -> bool:
        """Update a single field by key (flat storage)"""
        update_fields = {field_key: value}
        return await self.update_patient(patient_id, update_fields, organization_id)

    async def update_fields(
        self,
        patient_id: str,
        fields: dict,
        organization_id: str = None
    ) -> bool:
        """Update multiple fields (flat storage)"""
        return await self.update_patient(patient_id, fields, organization_id)

    async def update_call_status(self, patient_id: str, status: str, organization_id: str = None) -> bool:
        """Update call status"""
        return await self.update_patient(patient_id, {"call_status": status}, organization_id)
    
    async def save_call_transcript(
        self,
        patient_id: str,
        session_id: str,
        transcript_data: dict,
        organization_id: str = None
    ) -> bool:
        """Save call transcript and metadata"""
        update_fields = {
            "last_call_session_id": session_id,
            "last_call_timestamp": datetime.utcnow().isoformat(),
            "call_transcript": transcript_data
        }
        return await self.update_patient(patient_id, update_fields, organization_id)
    
    async def get_call_transcript(self, patient_id: str) -> Optional[dict]:
        """Get the last call transcript for a patient"""
        try:
            patient = await self.patients.find_one(
                {"_id": ObjectId(patient_id)},
                {"call_transcript": 1, "last_call_timestamp": 1, "last_call_session_id": 1}
            )
            return patient
        except Exception as e:
            logger.error(f"Error getting transcript for {patient_id}: {e}")
            return None
    
    async def delete_patient(self, patient_id: str, organization_id: str = None) -> bool:
        """Delete a patient by ObjectId"""
        try:
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            result = await self.patients.delete_one(query)
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting patient {patient_id}: {e}")
            return False


class AsyncUserRecord:
    """Async database operations for user records with HIPAA compliance"""

    MAX_FAILED_ATTEMPTS = 5
    PASSWORD_HISTORY_SIZE = 10
    PASSWORD_EXPIRY_DAYS = 90

    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[os.getenv("MONGO_DB_NAME", "alfons")]
        self.users = self.db.users

    async def _ensure_indexes(self):
        """Create indexes for user collection"""
        try:
            await self.users.create_index("email", unique=True)
            await self.users.create_index("organization_id")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    def check_password_complexity(self, password: str) -> tuple[bool, str]:
        """
        Validate password meets HIPAA complexity requirements:
        - Minimum 12 characters
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one number
        - At least one special character

        Returns:
            (is_valid, error_message)
        """
        if len(password) < 12:
            return False, "Password must be at least 12 characters long"

        if not any(c.isupper() for c in password):
            return False, "Password must contain at least one uppercase letter"

        if not any(c.islower() for c in password):
            return False, "Password must contain at least one lowercase letter"

        if not any(c.isdigit() for c in password):
            return False, "Password must contain at least one number"

        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            return False, "Password must contain at least one special character"

        return True, ""

    async def check_password_history(self, user_id: str, new_password: str) -> bool:
        """
        Check if password was used in the last 10 passwords.

        Returns:
            True if password is acceptable (not in history), False if reused
        """
        try:
            import bcrypt

            user = await self.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return True

            password_history = user.get("password_history", [])

            # Check against each historical password hash
            for old_hash in password_history:
                if bcrypt.checkpw(new_password.encode(), old_hash.encode()):
                    return False

            return True

        except Exception as e:
            logger.error(f"Error checking password history: {e}")
            return True  # Allow on error to not block user

    async def create_user(
        self,
        email: str,
        password: str,
        organization_id: str,
        created_by: Optional[str] = None,
        role: str = "user"
    ) -> Optional[str]:
        """
        Create a new user with hashed password.

        Args:
            email: User email (must be unique)
            password: Plain text password (will be hashed)
            organization_id: Organization this user belongs to (required)
            created_by: User ID of creator (for audit)
            role: User role (default: "user")

        Returns:
            User ID if successful, None if failed
        """
        try:
            import bcrypt
            from datetime import timedelta

            # Ensure indexes exist
            await self._ensure_indexes()

            # Validate password complexity
            is_valid, error_msg = self.check_password_complexity(password)
            if not is_valid:
                logger.warning(f"Password complexity check failed for {email}: {error_msg}")
                raise ValueError(error_msg)

            # Check if email already exists
            existing = await self.users.find_one({"email": email.lower()})
            if existing:
                logger.warning(f"Attempted to create duplicate user: {email}")
                raise ValueError("Email already registered")

            # Hash password
            hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

            now = datetime.utcnow()
            password_expires_at = now + timedelta(days=self.PASSWORD_EXPIRY_DAYS)

            user_data = {
                "email": email.lower(),
                "hashed_password": hashed_password,
                "password_history": [hashed_password],  # Initialize with first password
                "organization_id": ObjectId(organization_id),
                "role": role,
                "status": "active",
                "failed_login_attempts": 0,
                "locked_at": None,
                "locked_reason": None,
                "last_login_at": None,
                "last_password_change": now.isoformat(),
                "password_expires_at": password_expires_at.isoformat(),
                "created_at": now.isoformat(),
                "created_by": created_by,
                "updated_at": now.isoformat()
            }

            result = await self.users.insert_one(user_data)
            logger.info(f"Created new user: {email} (ID: {result.inserted_id})")
            return str(result.inserted_id)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating user {email}: {e}")
            return None

    async def get_users_by_organization(self, organization_id: str) -> List[dict]:
        """Get all users for an organization"""
        try:
            cursor = self.users.find(
                {"organization_id": ObjectId(organization_id)}
            ).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding users for org {organization_id}: {e}")
            return []

    async def find_user_by_email(self, email: str) -> Optional[dict]:
        """Find user by email address"""
        try:
            user = await self.users.find_one({"email": email.lower()})
            return user
        except Exception as e:
            logger.error(f"Error finding user {email}: {e}")
            return None

    async def verify_password(self, email: str, password: str) -> tuple[bool, Optional[dict]]:
        """
        Verify user password and return user data if valid.

        Returns:
            (is_valid, user_data)
        """
        try:
            import bcrypt

            user = await self.find_user_by_email(email)
            if not user:
                return False, None

            # Check if account is locked
            if user.get("status") == "locked":
                logger.warning(f"Login attempt for locked account: {email}")
                return False, None

            # Check if account is inactive
            if user.get("status") == "inactive":
                logger.warning(f"Login attempt for inactive account: {email}")
                return False, None

            # Verify password
            is_valid = bcrypt.checkpw(password.encode(), user["hashed_password"].encode())

            if is_valid:
                # Reset failed login attempts on successful login
                await self.users.update_one(
                    {"_id": user["_id"]},
                    {
                        "$set": {
                            "failed_login_attempts": 0,
                            "last_login_at": datetime.utcnow().isoformat()
                        }
                    }
                )
                return True, user
            else:
                # Increment failed login attempts
                new_count = user.get("failed_login_attempts", 0) + 1

                update_data = {
                    "failed_login_attempts": new_count,
                    "updated_at": datetime.utcnow().isoformat()
                }

                # Lock account if max attempts reached
                if new_count >= self.MAX_FAILED_ATTEMPTS:
                    update_data["status"] = "locked"
                    update_data["locked_at"] = datetime.utcnow().isoformat()
                    update_data["locked_reason"] = "Too many failed login attempts"
                    logger.warning(f"Account locked due to failed attempts: {email}")

                await self.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": update_data}
                )

                return False, None

        except Exception as e:
            logger.error(f"Error verifying password for {email}: {e}")
            return False, None

    async def update_password(
        self,
        user_id: str,
        new_password: str
    ) -> tuple[bool, str]:
        """
        Update user password with history tracking.

        Returns:
            (success, error_message)
        """
        try:
            import bcrypt
            from datetime import timedelta

            # Validate complexity
            is_valid, error_msg = self.check_password_complexity(new_password)
            if not is_valid:
                return False, error_msg

            # Check password history
            if not await self.check_password_history(user_id, new_password):
                return False, "Password was used recently. Please choose a different password."

            # Hash new password
            new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

            # Get current password history
            user = await self.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return False, "User not found"

            password_history = user.get("password_history", [])
            password_history.insert(0, new_hash)  # Add to front
            password_history = password_history[:self.PASSWORD_HISTORY_SIZE]  # Keep only last 10

            now = datetime.utcnow()
            password_expires_at = now + timedelta(days=self.PASSWORD_EXPIRY_DAYS)

            # Update password
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "hashed_password": new_hash,
                        "password_history": password_history,
                        "last_password_change": now.isoformat(),
                        "password_expires_at": password_expires_at.isoformat(),
                        "updated_at": now.isoformat()
                    }
                }
            )

            logger.info(f"Password updated for user {user_id}")
            return True, ""

        except Exception as e:
            logger.error(f"Error updating password for {user_id}: {e}")
            return False, str(e)

    async def generate_reset_token(self, email: str) -> tuple[bool, Optional[str]]:
        """Generate password reset token valid for 1 hour"""
        try:
            import secrets
            from datetime import timedelta

            user = await self.find_user_by_email(email)
            if not user:
                return False, None

            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=1)

            await self.users.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "reset_token": token,
                    "reset_token_expires": expires_at.isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )

            logger.info(f"Reset token generated for {email}")
            return True, token

        except Exception as e:
            logger.error(f"Error generating reset token for {email}: {e}")
            return False, None

    async def verify_reset_token(self, email: str, token: str) -> tuple[bool, Optional[str]]:
        """Verify reset token and return user_id if valid"""
        try:
            user = await self.find_user_by_email(email)
            if not user:
                return False, None

            stored_token = user.get("reset_token")
            expires_str = user.get("reset_token_expires")

            if not stored_token or not expires_str:
                return False, None

            if stored_token != token:
                return False, None

            expires_at = datetime.fromisoformat(expires_str)
            if datetime.utcnow() > expires_at:
                logger.warning(f"Expired reset token used for {email}")
                return False, None

            return True, str(user["_id"])

        except Exception as e:
            logger.error(f"Error verifying reset token for {email}: {e}")
            return False, None

    async def reset_password_with_token(
        self,
        email: str,
        token: str,
        new_password: str
    ) -> tuple[bool, str]:
        """Reset password using valid token"""
        try:
            is_valid, user_id = await self.verify_reset_token(email, token)
            if not is_valid or not user_id:
                return False, "Invalid or expired reset token"

            success, error = await self.update_password(user_id, new_password)
            if not success:
                return False, error

            # Clear reset token after successful reset
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$unset": {"reset_token": "", "reset_token_expires": ""}}
            )

            logger.info(f"Password reset successful for {email}")
            return True, ""

        except Exception as e:
            logger.error(f"Error resetting password for {email}: {e}")
            return False, str(e)

    async def lock_account(self, user_id: str, reason: str) -> bool:
        """Lock a user account"""
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "status": "locked",
                        "locked_at": datetime.utcnow().isoformat(),
                        "locked_reason": reason,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                }
            )
            logger.info(f"Account locked: {user_id} - Reason: {reason}")
            return True
        except Exception as e:
            logger.error(f"Error locking account {user_id}: {e}")
            return False

    async def unlock_account(self, user_id: str, unlocked_by: str) -> bool:
        """Unlock a user account"""
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "status": "active",
                        "locked_at": None,
                        "locked_reason": None,
                        "failed_login_attempts": 0,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                }
            )
            logger.info(f"Account unlocked: {user_id} by {unlocked_by}")
            return True
        except Exception as e:
            logger.error(f"Error unlocking account {user_id}: {e}")
            return False

    async def deactivate_user(self, user_id: str, deactivated_by: str) -> bool:
        """Deactivate a user account (soft delete)"""
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "status": "inactive",
                        "deactivated_at": datetime.utcnow().isoformat(),
                        "deactivated_by": deactivated_by,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                }
            )
            logger.info(f"Account deactivated: {user_id} by {deactivated_by}")
            return True
        except Exception as e:
            logger.error(f"Error deactivating account {user_id}: {e}")
            return False


# Singleton pattern for database client
_async_client: Optional[AsyncIOMotorClient] = None

def get_async_patient_db() -> AsyncPatientRecord:
    """Get or create async patient database instance"""
    global _async_client
    if not _async_client:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        logger.info(f"Connecting to MongoDB: {mongo_uri}")
        _async_client = AsyncIOMotorClient(mongo_uri)

    return AsyncPatientRecord(_async_client)

def get_async_user_db() -> AsyncUserRecord:
    """Get or create async user database instance"""
    global _async_client
    if not _async_client:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        logger.info(f"Connecting to MongoDB: {mongo_uri}")
        _async_client = AsyncIOMotorClient(mongo_uri)

    return AsyncUserRecord(_async_client)