"""Every role that actually runs an agent turn needs a soul file, or
`RoleSoulProvider` silently contributes nothing for it -- not a crash, just
an agent with no persona/instructions, which is easy to miss (this
happened for real: builder/reviewer/qa shipped in config/agents.yaml and
config/workflow.yaml well before their souls existed). This test makes
that omission loud instead of silent.
"""

from agentic_dev.domain.workflow import WorkflowDefinition


def test_every_agent_running_workflow_role_has_a_soul_file(template_config_dir):
    workflow = WorkflowDefinition.load(template_config_dir / "workflow.yaml")
    roles = {state.role for state in workflow.states.values() if state.runs_agent}

    souls_dir = template_config_dir / "souls"
    missing = [role for role in roles if not (souls_dir / f"{role}.md").exists()]

    assert missing == []
