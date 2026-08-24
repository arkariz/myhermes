import sys, pathlib
sys.path.insert(0, ".")
import orchestrator.cli as cli

cli.CONFIG_DIR = pathlib.Path("/tmp/demo/config")
cli.REPO_ROOT = pathlib.Path(".")

def run(args):
    print(f"\n$ orchestrator {' '.join(args)}")
    rc = cli.main(args)
    print(f"  (exit {rc})")
    return rc

run(["project", "new", "toy", "--host-path", "/tmp/demo/host/toy",
     "--state-path", "/tmp/demo/agent-state/toy"])
run(["approve", "toy", "START_PROJECT"])
run(["turn", "toy", "Build a habit tracker for a single user. Track daily check-ins."])
run(["turn", "toy", "Make it support two users sharing one tracker instead of one."])
run(["status", "toy"])
run(["approve", "toy", "APPROVE_DISCOVERY"])
run(["status", "toy"])
run(["turn", "toy", "Draft the PRD now with acceptance criteria."])
run(["approve", "toy", "APPROVE_PRD"])
run(["status", "toy"])
