from typing import Callable # Changed from List, Callable; asyncio removed
import dspy
from global_config import global_config

from loguru import logger as log
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from litellm import ServiceUnavailableError


class ReactAgent: # Renamed from ReactAgentWithMemory
    def __init__(
        self,
        agent_signature: dspy.Signature,
        tools: list[Callable] = [], # Changed List to list
        model_name: str = global_config.agent.chat_agent_model,
    ):
        api_key = global_config.llm_api_key(model_name)
        self.lm = dspy.LM(
            model=model_name,
            api_key=api_key,
            # cache=global_config.llm_cache_enabled,
            temperature=global_config.weird_quirk.temperature,
            max_tokens=global_config.weird_quirk.max_tokens,
        )
        dspy.configure(lm=self.lm)

        # Agent Intiialization
        self.agent_init = dspy.ReAct(
            agent_signature,
            tools=tools, # Uses tools as passed, no longer appends read_memory
        )
        self.agent = dspy.asyncify(self.agent_init)

    @retry(
        retry=retry_if_exception_type(ServiceUnavailableError),
        stop=stop_after_attempt(global_config.llm_config.retry.max_attempts),
        wait=wait_exponential(
            multiplier=global_config.llm_config.retry.min_wait_seconds,
            max=global_config.llm_config.retry.max_wait_seconds
        ),
        before_sleep=lambda retry_state: log.warning(
            f"Retrying due to ServiceUnavailableError. Attempt {retry_state.attempt_number}"
        )
    )
    async def run(
        self,
        user_id: str,
        **kwargs,
    ):
        try:
            # user_id is passed if the agent_signature requires it.
            result = await self.agent(**kwargs, lm=self.lm, user_id=user_id)
        except Exception as e:
            log.error(f"Error in run: {str(e)}")
            raise e
        return result