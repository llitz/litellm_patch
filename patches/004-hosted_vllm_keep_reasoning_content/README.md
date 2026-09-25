# 004 — hosted_vllm_keep_reasoning_content

Since litellm v1.100.0, the `hosted_vllm` message transform removes
`reasoning_content` from assistant messages before sending them to the backend.
A multi-turn agent that replays its own conversation history therefore silently
loses the model's prior reasoning on every turn: the chat template renders an
empty `<think>` block for each earlier assistant turn, so the model re-derives
reasoning from scratch and long agentic sessions drift. No error is raised.

vLLM accepts `reasoning_content` on incoming assistant messages and its chat
template renders it back into each prior assistant turn, so the removal is
unnecessary. The same one-line fix was merged upstream for `fireworks_ai`
(BerriAI/litellm#40682); open upstream PRs for this provider are
BerriAI/litellm#41380, #41396, #42940. The regression came in via
BerriAI/litellm#37953 (commit `32bf1aba2`), tracked as BerriAI/litellm#41392.

This patch deletes that one line. `thinking_blocks` removal and content-list
flattening are untouched — only `reasoning_content` survives.

- Base: litellm v1.102.1
- File: `litellm/llms/hosted_vllm/chat/transformation.py`
- Patch: `hosted_vllm_keep_reasoning_content.diff` (`patch -p1` / `git apply`,
  from the litellm source root)

## Applying

    git clone --depth 1 --branch v1.102.1 https://github.com/BerriAI/litellm
    cd litellm
    git apply /path/to/004-hosted_vllm_keep_reasoning_content/hosted_vllm_keep_reasoning_content.diff

Then mount the resulting file read-only over the module path in the proxy image:

    - ./litellm/llms/hosted_vllm/chat/transformation.py:/app/.venv/lib/python3.13/site-packages/litellm/llms/hosted_vllm/chat/transformation.py:ro

Restart the proxy after mounting — mounted files take effect on container start
only.

## Notes

Independent of patches 001–003: it touches a different file
(`litellm/llms/hosted_vllm/chat/transformation.py`), so it applies cleanly to
pristine stock v1.102.1 on its own and in any order with the others. The
removal is still present in stock v1.102.1, so the patch's diff is unchanged
from the v1.100.0 version.

This increases prompt size for agentic sessions, because replayed reasoning is
no longer discarded. That is the intended contract for reasoning models replaying
their own history, and it is what the direct-to-vLLM path already does.

Drop the mount once an upstream fix for this provider merges into the image's
litellm version.
