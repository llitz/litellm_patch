from litellm.integrations.custom_logger import CustomLogger

class ZaiThinkingHook(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        model = kwargs.get("model", "")
        if not (model.startswith("zai/") or kwargs.get("custom_llm_provider") == "zai"):
            return None
        extra_body = kwargs.get("extra_body") or {}
        changed = False
        for key in ("thinking", "reasoning_effort"):
            value = kwargs.pop(key, None)          # None-guard: hook fires multiple times per request
            if value is not None:
                extra_body[key] = value
                changed = True
        if changed:
            kwargs["extra_body"] = extra_body
        return kwargs

zai_thinking_hook_instance = ZaiThinkingHook()
