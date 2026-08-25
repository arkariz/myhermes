"""Every role that actually runs an agent turn needs a soul file, or
`RoleSoulProvider` silently contributes nothing for it -- not a crash, just
an agent with no persona/instructions, which is easy to miss (this
happened for real: builder/reviewer/qa shipped in config/agents.yaml and
config/workflow.yaml well before their souls existed). This test makes
that omission loud instead of silent.
"""

from pathlib import Path

from orchestrator.state_machine import WorkflowDefinition

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def test_every_agent_running_workflow_role_has_a_soul_file():
    workflow = WorkflowDefinition.load(CONFIG_DIR / "workflow.yaml")
    roles = {state.role for state in workflow.states.values() if state.runs_agent}

    missing = [role for role in roles if not (CONFIG_DIR / "souls" / f"{role}.md").exists()]

    assert missing == []
