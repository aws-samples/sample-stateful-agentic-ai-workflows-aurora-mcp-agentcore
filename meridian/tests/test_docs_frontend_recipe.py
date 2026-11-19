"""The frontend test recipe in the README installs the browser the way CI does."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "application-ci.yml").read_text(encoding="utf-8")
INSTALL = "npx playwright install --with-deps chromium"


def recipe() -> list[str]:
    block = README.split("Frontend, from `meridian/frontend/`, one command at a time")[1]
    return block.split("```bash\n")[1].split("```")[0].splitlines()


def test_ci_installs_chromium_before_the_accessibility_suite():
    steps = [line.strip().removeprefix("- run: ") for line in CI.splitlines()]

    assert steps.index(INSTALL) < steps.index("npm run test:accessibility")


def test_the_readme_recipe_has_the_same_install_step_before_the_suite():
    commands = recipe()

    assert commands.index(INSTALL) == commands.index("npm run test:accessibility") - 1
    assert commands[:5] == ["npm ci", "npm run lint", "npm run typecheck", "npm run test:run",
                            "npm run build"]


def test_the_readme_explains_the_linux_dependencies_and_the_other_platforms():
    assert "operating-system libraries" in README and "`apt`" in README
    assert "npx playwright install chromium" in README
    assert "missing-executable error" in README
