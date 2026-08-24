import sys
sys.path.insert(0, "/workspace/repo")
from pathlib import Path
from runtime.hermes import HermesRequest, run

home = Path("/tmp/agent-state/toy-project/.hermes-home")

# Full turn (-z)
req1 = HermesRequest(
    prompt="The project's codename for this chat is FALCON. Just say OK.",
    home_dir=home,
    provider="openrouter",
    model="openai/gpt-4o-mini",
)
r1 = run(req1, usage_file=Path("/tmp/usage1.json"))
print("TURN 1 (full):", repr(r1.response), "| session:", r1.session_id, "| failed:", r1.failed)
print("  usage:", r1.usage.get("input_tokens"), r1.usage.get("cache_read_tokens"))

# Continuation turn (chat -q --resume)
req2 = HermesRequest(
    prompt="What is the project's codename?",
    home_dir=home,
    provider="openrouter",
    model="openai/gpt-4o-mini",
    resume_session_id=r1.session_id,
)
r2 = run(req2, usage_file=None)
print("TURN 2 (continuation):", repr(r2.response), "| session:", r2.session_id, "| failed:", r2.failed)
print("  usage:", r2.usage.get("input_tokens"), r2.usage.get("cache_read_tokens"))
