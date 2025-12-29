from enum import Enum
from typing import Optional


class ContextStrategy(Enum):
    APPEND = "APPEND"
    RESET = "RESET"
    RESET_WITH_SUMMARY = "RESET_WITH_SUMMARY"


class EvalContextManager:

    def __init__(self):
        self.role_messages: list[dict] = []
        self.task_messages: list[dict] = []
        self.conversation_history: list[dict] = []
        self._current_node_name: Optional[str] = None
        self._is_first_node = True

    def set_node(self, node: dict) -> None:
        node_name = node.get("name", "unknown")
        role_messages = node.get("role_messages") or []
        task_messages = node.get("task_messages") or []

        context_config = node.get("context_strategy") or {}
        strategy_value = context_config.get("strategy") if isinstance(context_config, dict) else None

        if strategy_value == "RESET" or (hasattr(strategy_value, 'name') and strategy_value.name == "RESET"):
            strategy = ContextStrategy.RESET
        elif strategy_value == "RESET_WITH_SUMMARY" or (hasattr(strategy_value, 'name') and strategy_value.name == "RESET_WITH_SUMMARY"):
            strategy = ContextStrategy.RESET_WITH_SUMMARY
        else:
            strategy = ContextStrategy.APPEND

        if self._is_first_node or strategy in [ContextStrategy.RESET, ContextStrategy.RESET_WITH_SUMMARY]:
            self.role_messages = list(role_messages)
            self.task_messages = list(task_messages)

            if strategy in [ContextStrategy.RESET, ContextStrategy.RESET_WITH_SUMMARY]:
                self.conversation_history = []

            self._is_first_node = False
        else:
            # APPEND strategy: keep conversation history, but replace (not extend) role/task messages
            # Role messages define bot persona - should be replaced if new node has them
            if role_messages:
                self.role_messages = list(role_messages)
            self.task_messages = list(task_messages)

        self._current_node_name = node_name

    def add_user_message(self, content: str) -> None:
        self.conversation_history.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        if content:
            self.conversation_history.append({"role": "assistant", "content": content})

    def add_tool_call(self, tool_call: dict) -> None:
        self.conversation_history.append({
            "role": "assistant",
            "content": tool_call.get("content"),
            "tool_calls": tool_call.get("tool_calls", []),
        })

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        # Production returns JSON for transition functions
        result_content = content if content else '{"status": "acknowledged"}'
        self.conversation_history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        })

    def get_messages(self) -> list[dict]:
        return self.role_messages + self.task_messages + self.conversation_history

    @property
    def current_node(self) -> str:
        return self._current_node_name or "unknown"
