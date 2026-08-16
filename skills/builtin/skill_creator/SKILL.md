---
name: regpilot-skill-creator
description: Use when an operator wants RegPilot to create, inspect, adapt, validate, install, enable, or load a local Agent Skill package from an explicit local folder or SKILL.md path.
---

# RegPilot Skill Creator

Use this built-in workflow to bring a local external skill package into RegPilot without giving the model free filesystem or command-line access.

Read `reference/local-package-install.md` before installing or enabling a local skill package.

## Workflow

1. Identify the explicit local skill package path from the operator message. Do not infer hidden paths.
2. Call `regpilot_inspect_skill` for the package path.
3. If inspection returns `location=collection`, show the candidates and ask the operator which specific skill package to use.
4. If inspection returns `status=local_source`, explain that the package is not installed yet.
5. Explain the detected `skill_type`, title, resource folders, validation warnings, and whether scripts are present.
6. Call `regpilot_validate_skill`.
7. If validation passes and the operator wants to proceed, call `regpilot_install_skill`.
8. Keep the installed skill disabled until the operator explicitly asks to enable it.
9. Call `regpilot_enable_skill` only for an installed or built-in skill.
10. Call `regpilot_load_skill` only after enabling, so the model can read the skill instructions and references.

## Boundaries

- Local package only: do not download, clone, browse registries, or fetch remote archives.
- Do not execute scripts from the package during inspect, validate, install, enable, or load.
- Treat scripts as untrusted unless a future explicit trust workflow says otherwise.
- Do not rewrite an external skill silently. If adaptation is needed, create or install a reviewed draft and explain the issue.
- Do not expose operation nodes as separate operator skills.
