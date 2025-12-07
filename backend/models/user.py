from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING
from bson import ObjectId
from loguru import logger

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient

from backend.database import get_mongo_client, MONGO_DB_NAME


class AsyncUserRecord:
    MAX_FAILED_ATTEMPTS = 5
    PASSWORD_HISTORY_SIZE = 10
    PASSWORD_EXPIRY_DAYS = 90

    def __init__(self, db_client: "AsyncIOMotorClient"):
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
            now = datetime.now(timezone.utc)
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
                    {"$set": {"failed_login_attempts": 0, "last_login_at": datetime.now(timezone.utc).isoformat()}}
                )
                return True, user
            else:
                new_count = user.get("failed_login_attempts", 0) + 1
                update_data = {"failed_login_attempts": new_count, "updated_at": datetime.now(timezone.utc).isoformat()}

                if new_count >= self.MAX_FAILED_ATTEMPTS:
                    update_data["status"] = "locked"
                    update_data["locked_at"] = datetime.now(timezone.utc).isoformat()
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

            now = datetime.now(timezone.utc)
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
            expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

            await self.users.update_one(
                {"_id": user["_id"]},
                {"$set": {
                    "reset_token": token,
                    "reset_token_expires": expires_at.isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
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
            if datetime.now(timezone.utc) > expires_at:
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
                    "locked_at": datetime.now(timezone.utc).isoformat(),
                    "locked_reason": reason,
                    "updated_at": datetime.now(timezone.utc).isoformat()
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
                    "updated_at": datetime.now(timezone.utc).isoformat()
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
                    "deactivated_at": datetime.now(timezone.utc).isoformat(),
                    "deactivated_by": deactivated_by,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            logger.info(f"Account deactivated: {user_id} by {deactivated_by}")
            return True
        except Exception as e:
            logger.error(f"Error deactivating account {user_id}: {e}")
            return False

    async def set_handoff_token(self, user_id: str, token: str, expires_at: datetime) -> bool:
        """Set a single-use handoff token for central login flow."""
        try:
            result = await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "handoff_token": token,
                    "handoff_token_expires": expires_at.isoformat(),
                    "handoff_token_used": False,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error setting handoff token for {user_id}: {e}")
            return False

    async def validate_handoff_token(self, token: str) -> Optional[dict]:
        """Validate and return user if handoff token is valid and unused."""
        try:
            user = await self.users.find_one({
                "handoff_token": token,
                "handoff_token_used": False
            })

            if not user:
                return None

            expires_str = user.get("handoff_token_expires", "")
            if not expires_str:
                return None

            expires = datetime.fromisoformat(expires_str)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) > expires:
                logger.warning(f"Expired handoff token used for user {user['_id']}")
                return None

            return user
        except Exception as e:
            logger.error(f"Error validating handoff token: {e}")
            return None

    async def clear_handoff_token(self, user_id: str) -> bool:
        """Mark handoff token as used (single-use enforcement)."""
        try:
            result = await self.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "handoff_token_used": True,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error clearing handoff token for {user_id}: {e}")
            return False


_user_db_instance: Optional[AsyncUserRecord] = None


def get_async_user_db() -> AsyncUserRecord:
    global _user_db_instance
    if _user_db_instance is None:
        _user_db_instance = AsyncUserRecord(get_mongo_client())
    return _user_db_instance
