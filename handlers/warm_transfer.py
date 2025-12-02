from loguru import logger
from pipecat.transports.daily.transport import DailyTransport


def get_participant_by_user_id(transport: DailyTransport, user_id: str) -> str | None:
    return next(
        (
            p["id"]
            for p in transport.participants().values()
            if not p["info"]["isLocal"] and p["info"].get("userId") == user_id
        ),
        None,
    )


async def mute_participant(transport: DailyTransport, user_id: str) -> bool:
    participant_id = get_participant_by_user_id(transport, user_id)
    if participant_id:
        await transport.update_remote_participants(
            remote_participants={participant_id: {"permissions": {"canSend": []}}}
        )
        logger.info(f"Muted participant: {user_id}")
        return True
    logger.warning(f"Could not find participant to mute: {user_id}")
    return False


async def unmute_participant(transport: DailyTransport, user_id: str) -> bool:
    participant_id = get_participant_by_user_id(transport, user_id)
    if participant_id:
        await transport.update_remote_participants(
            remote_participants={
                participant_id: {
                    "permissions": {"canSend": ["microphone"]},
                    "inputsEnabled": {"microphone": True},
                }
            }
        )
        logger.info(f"Unmuted participant: {user_id}")
        return True
    logger.warning(f"Could not find participant to unmute: {user_id}")
    return False


async def isolate_participant_audio(
    transport: DailyTransport, user_id: str, can_hear_user_ids: list[str]
) -> bool:
    participant_id = get_participant_by_user_id(transport, user_id)
    if participant_id:
        by_user_id = {uid: True for uid in can_hear_user_ids}
        await transport.update_remote_participants(
            remote_participants={
                participant_id: {
                    "permissions": {"canReceive": {"base": False, "byUserId": by_user_id}}
                }
            }
        )
        logger.info(f"Isolated audio for {user_id}, can hear: {can_hear_user_ids}")
        return True
    logger.warning(f"Could not find participant to isolate: {user_id}")
    return False


async def connect_participants(
    transport: DailyTransport, user_id_a: str, user_id_b: str
) -> bool:
    pid_a = get_participant_by_user_id(transport, user_id_a)
    pid_b = get_participant_by_user_id(transport, user_id_b)

    if pid_a and pid_b:
        await transport.update_remote_participants(
            remote_participants={
                pid_a: {
                    "permissions": {
                        "canSend": ["microphone"],
                        "canReceive": {"byUserId": {user_id_b: True}},
                    },
                    "inputsEnabled": {"microphone": True},
                },
                pid_b: {
                    "permissions": {
                        "canSend": ["microphone"],
                        "canReceive": {"byUserId": {user_id_a: True}},
                    },
                    "inputsEnabled": {"microphone": True},
                },
            }
        )
        logger.info(f"Connected participants: {user_id_a} <-> {user_id_b}")
        return True
    logger.warning(f"Could not find participants to connect: {user_id_a}, {user_id_b}")
    return False
