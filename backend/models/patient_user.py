import logging
from datetime import datetime
from typing import Optional, List, Any
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

from backend.database import get_mongo_client, MONGO_DB_NAME

logger = logging.getLogger(__name__)


class AsyncPatientRecord:
    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.patients = self.db.patients

    async def _ensure_indexes(self):
        try:
            await self.patients.create_index([("organization_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    async def find_patient_by_id(self, patient_id: str, organization_id: str = None) -> Optional[dict]:
        try:
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            return await self.patients.find_one(query)
        except Exception as e:
            logger.error(f"Error finding patient {patient_id}: {e}")
            return None

    async def find_patients_by_organization(self, organization_id: str, workflow: str = None) -> List[dict]:
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
        try:
            await self._ensure_indexes()
            now = datetime.utcnow().isoformat()

            if "organization_id" in patient_data and isinstance(patient_data["organization_id"], str):
                patient_data["organization_id"] = ObjectId(patient_data["organization_id"])

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
        try:
            update_fields["updated_at"] = datetime.utcnow().isoformat()
            query = {"_id": ObjectId(patient_id)}
            if organization_id:
                query["organization_id"] = ObjectId(organization_id)
            result = await self.patients.update_one(query, {"$set": update_fields})
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating patient {patient_id}: {e}")
            return False

    async def update_field(self, patient_id: str, field_key: str, value: Any, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, {field_key: value}, organization_id)

    async def update_fields(self, patient_id: str, fields: dict, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, fields, organization_id)

    async def update_call_status(self, patient_id: str, status: str, organization_id: str = None) -> bool:
        return await self.update_patient(patient_id, {"call_status": status}, organization_id)

    async def save_call_transcript(
        self,
        patient_id: str,
        session_id: str,
        transcript_data: dict,
        organization_id: str = None
    ) -> bool:
        update_fields = {
            "last_call_session_id": session_id,
            "last_call_timestamp": datetime.utcnow().isoformat(),
            "call_transcript": transcript_data
        }
        return await self.update_patient(patient_id, update_fields, organization_id)

    async def get_call_transcript(self, patient_id: str) -> Optional[dict]:
        try:
            return await self.patients.find_one(
                {"_id": ObjectId(patient_id)},
                {"call_transcript": 1, "last_call_timestamp": 1, "last_call_session_id": 1}
            )
        except Exception as e:
            logger.error(f"Error getting transcript for {patient_id}: {e}")
            return None

    async def delete_patient(self, patient_id: str, organization_id: str = None) -> bool:
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
    MAX_FAILED_ATTEMPTS = 5
    PASSWORD_HISTORY_SIZE = 10
    PASSWORD_EXPIRY_DAYS = 90

    def __init__(self, db_client: AsyncIOMotorClient):
        self.client = db_client
        self.db = db_client[MONGO_DB_NAME]
        self.users = self.db.users

    async def _ensure_indexes(self):
        try:
            await self.users.create_index("email", unique=True)
            await self.users.create_index("organization_id")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    def check_password_complexity(self, password: str) -> tuple[bool, str]:
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
        try:
            import bcrypt
            user = await self.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return True
            password_history = user.get("password_history", [])
            for old_hash in password_history:
                if bcrypt.checkpw(new_password.encode(), old_hash.encode()):
                    return False
            return True
        except Exception as e:
            logger.error(f"Error checking password history: {e}")
            return True

    async def create_user(
        self,
        email: str,
        password: str,
        organization_id: str,
        created_by: Optional[str] = None,
        role: str = "user"
    ) -> Optional[str]:
        try:
            import bcrypt
            from datetime import timedelta

            await self._ensure_indexes()

            is_valid, error_msg = self.check_password_complexity(password)
            if not is_valid:
                logger.warning(f"Password complexity check failed for {email}: {error_msg}")
                raise ValueError(error_msg)

            existing = await self.users.find_one({"email": email.lower()})
            if existing:
                logger.warning(f"Attempted to create duplicate user: {email}")
                raise ValueError("Email already registered")

            hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            now = datetime.utcnow()
            password_expires_at = now + timedelta(days=self.PASSWORD_EXPIRY_DAYS)

            user_data = {
                "email": email.lower(),
                "hashed_password": hashed_password,
                "password_history": [hashed_password],
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
        try:
            cursor = self.users.find({"organization_id": ObjectId(organization_id)}).sort("created_at", -1)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding users for org {organization_id}: {e}")
            return []

    async def find_user_by_email(self, email: str) -> Optional[dict]:
        try:
            return await self.users.find_one({"email": email.lower()})
        except Exception as e:
            logger.error(f"Error finding user {email}: {e}")
            return None

    async def verify_password(self, email: str, password: str) -> tuple[bool, Optional[dict]]:
        try:
            import bcrypt

            user = await self.find_user_by_email(email)
            if not user:
                return False, None

            if user.get("status") == "locked":
                logger.warning(f"Login attempt for locked account: {email}")
                return False, None

            if user.get("status") == "inactive":
                logger.warning(f"Login attempt for inactive account: {email}")
                return False, None

            is_valid = bcrypt.checkpw(password.encode(), user["hashed_password"].encode())

            if is_valid:
                await self.users.update_one(
                    {"_id": user["_id"]},
                    {"$set": {"failed_login_attempts": 0, "last_login_at": datetime.utcnow().isoformat()}}
                )
                return True, user
            else:
                new_count = user.get("failed_login_attempts", 0) + 1
                update_data = {"failed_login_attempts": new_count, "updated_at": datetime.utcnow().isoformat()}

                if new_count >= self.MAX_FAILED_ATTEMPTS:
                    update_data["status"] = "locked"
                    update_data["locked_at"] = datetime.utcnow().isoformat()
                    update_data["locked_reason"] = "Too many failed login attempts"
                    logger.warning(f"Account locked due to failed attempts: {email}")

                await self.users.update_one({"_id": user["_id"]}, {"$set": update_data})
                return False, None
        except Exception as e:
            logger.error(f"Error verifying password for {email}: {e}")
            return False, None

    async def update_password(self, user_id: str, new_password: str) -> tuple[bool, str]:
        try:
            import bcrypt
            from datetime import timedelta

            is_valid, error_msg = self.check_password_complexity(new_password)
            if not is_valid:
                return False, error_msg

            if not await self.check_password_history(user_id, new_password):
                return False, "Password was used recently. Please choose a different password."

            new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

            user = await self.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return False, "User not found"

            password_history = user.get("password_history", [])
            password_history.insert(0, new_hash)
            password_history = password_history[:self.PASSWORD_HISTORY_SIZE]

            now = datetime.utcnow()
            password_expires_at = now + timedelta(days=self.PASSWORD_EXPIRY_DAYS)

            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "hashed_password": new_hash,
                    "password_history": password_history,
                    "last_password_change": now.isoformat(),
                    "password_expires_at": password_expires_at.isoformat(),
                    "updated_at": now.isoformat()
                }}
            )

            logger.info(f"Password updated for user {user_id}")
            return True, ""
        except Exception as e:
            logger.error(f"Error updating password for {user_id}: {e}")
            return False, str(e)

    async def generate_reset_token(self, email: str) -> tuple[bool, Optional[str]]:
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

    async def reset_password_with_token(self, email: str, token: str, new_password: str) -> tuple[bool, str]:
        try:
            is_valid, user_id = await self.verify_reset_token(email, token)
            if not is_valid or not user_id:
                return False, "Invalid or expired reset token"

            success, error = await self.update_password(user_id, new_password)
            if not success:
                return False, error

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
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "status": "locked",
                    "locked_at": datetime.utcnow().isoformat(),
                    "locked_reason": reason,
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )
            logger.info(f"Account locked: {user_id} - Reason: {reason}")
            return True
        except Exception as e:
            logger.error(f"Error locking account {user_id}: {e}")
            return False

    async def unlock_account(self, user_id: str, unlocked_by: str) -> bool:
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "status": "active",
                    "locked_at": None,
                    "locked_reason": None,
                    "failed_login_attempts": 0,
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )
            logger.info(f"Account unlocked: {user_id} by {unlocked_by}")
            return True
        except Exception as e:
            logger.error(f"Error unlocking account {user_id}: {e}")
            return False

    async def deactivate_user(self, user_id: str, deactivated_by: str) -> bool:
        try:
            await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "status": "inactive",
                    "deactivated_at": datetime.utcnow().isoformat(),
                    "deactivated_by": deactivated_by,
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )
            logger.info(f"Account deactivated: {user_id} by {deactivated_by}")
            return True
        except Exception as e:
            logger.error(f"Error deactivating account {user_id}: {e}")
            return False


_patient_db_instance: Optional[AsyncPatientRecord] = None
_user_db_instance: Optional[AsyncUserRecord] = None


def get_async_patient_db() -> AsyncPatientRecord:
    global _patient_db_instance
    if _patient_db_instance is None:
        _patient_db_instance = AsyncPatientRecord(get_mongo_client())
    return _patient_db_instance


def get_async_user_db() -> AsyncUserRecord:
    global _user_db_instance
    if _user_db_instance is None:
        _user_db_instance = AsyncUserRecord(get_mongo_client())
    return _user_db_instance
